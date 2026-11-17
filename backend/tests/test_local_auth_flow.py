"""Opt-in smoke test against a disposable Supabase CLI project and its Mailpit."""

import base64
import hmac
import html
import os
import re
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.database import postgres_url
from app.db_admin import bootstrap_admin
from app.main import create_app
from app.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("FN_RUN_LOCAL_AUTH") != "1",
    reason="Requer projeto Supabase CLI local e descartável. Consulte docs/TESTES.md.",
)
ROOT = Path(__file__).resolve().parents[2]


def local_endpoint(url, port):
    target = urlsplit(url)
    assert target.hostname == "127.0.0.1" and target.port == port


def check(response, status):
    # Auth responses may contain access tokens or authenticator keys.
    if response.status_code != status:
        code = response.headers.get("x-sb-error-code")
        context = f" ({code})" if code else ""
        pytest.fail(f"{response.request.method} {response.request.url.path}: {response.status_code}{context}")
    return response.json() if response.content else None


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def totp_code(secret):
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = struct.pack(">Q", int(time.time()) // 30)
    digest = hmac.digest(key, counter, "sha1")
    offset = digest[-1] & 0xF
    value = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def enroll_factor(auth, activation_token):
    headers = bearer(activation_token)
    return check(auth.post("/factors", json={"factor_type": "totp"}, headers=headers), 200)


def activate_mfa(auth, activation_token, factor):
    headers = bearer(activation_token)
    url = f"/factors/{factor['id']}"
    challenge = check(auth.post(f"{url}/challenge", json={}, headers=headers), 200)
    verified = check(auth.post(f"{url}/verify", json={
        "challenge_id": challenge["id"], "code": totp_code(factor["totp"]["secret"]),
    }, headers=headers), 200)
    return verified["access_token"]


def mail_token(mail, email, kind):
    subject = "convite de acesso" if kind == "invite" else "recuperação de senha"
    expected_route = "/ativar-conta" if kind == "invite" else "/recuperar-senha"
    for _ in range(20):
        messages = check(mail.get("/api/v1/messages", params={"limit": 50}), 200)["messages"]
        for message in messages:
            recipients = {item["Address"] for item in message["To"]}
            if email not in recipients or subject not in message["Subject"]:
                continue
            full = check(mail.get(f"/api/v1/message/{message['ID']}"), 200)
            hrefs = re.findall(r'href="([^"]+)"', full["HTML"])
            for href in hrefs:
                parsed = urlsplit(html.unescape(href))
                if parsed.path == expected_route:
                    assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:8088"
                    assert not parsed.query
                    fragments = parse_qs(parsed.fragment)
                    assert fragments["type"] == [kind]
                    return fragments["token_hash"][0]
        time.sleep(0.25)
    pytest.fail(f"Mailpit did not receive the {kind} email")


def test_login_invitation_recovery_and_tenant_isolation():
    values = dotenv_values(ROOT / ".env.local")
    url = values["FN_SUPABASE_URL"]
    local_endpoint(url, 54321)
    local_endpoint(values["FN_DATABASE_URL"], 54322)
    local_endpoint(values["FN_MIGRATION_DATABASE_URL"], 54322)
    settings = Settings(
        _env_file=None,
        environment="local",
        database_url=values["FN_DATABASE_URL"],
        supabase_url=url,
        supabase_issuer=values["FN_SUPABASE_ISSUER"],
        supabase_publishable_key=values["FN_SUPABASE_PUBLISHABLE_KEY"],
        supabase_admin_key=values["FN_SUPABASE_ADMIN_KEY"],
        auth_mode=values["FN_AUTH_MODE"],
        frontend_url="http://127.0.0.1:8088",
        cors_origins=["http://127.0.0.1:8088"],
        static_dir=ROOT / "dist",
    )
    owner = create_engine(postgres_url(values["FN_MIGRATION_DATABASE_URL"]), hide_parameters=True)
    key = values["FN_SUPABASE_ADMIN_KEY"]
    admin_api = {"apikey": key, **({} if key.startswith("sb_secret_") else bearer(key))}
    run_id = uuid4().hex[:12]
    slug_a, slug_b = f"e2e-{run_id}-a", f"e2e-{run_id}-b"
    admin_mail = f"e2e-admin-{run_id}@example.com"
    client_mail = f"e2e-client-{run_id}@example.com"
    temporary_mail = f"e2e-temporary-{run_id}@example.com"
    pending_mail = f"e2e-pending-{run_id}@example.com"
    unconfirmed_mail = f"e2e-unconfirmed-{run_id}@example.com"
    pwd = secrets.token_urlsafe(24)

    with owner.begin() as db:
        assert not db.scalar(text("SELECT 1 FROM futuranext.platform_roles WHERE role = 'super_admin'")), (
            "Use projeto de testes sem administrador persistente para o bootstrap isolado."
        )

    with (
        httpx.Client(base_url=f"{url}/auth/v1", timeout=5, trust_env=False,
                     headers={"apikey": values["FN_SUPABASE_PUBLISHABLE_KEY"]}) as auth,
        httpx.Client(base_url="http://127.0.0.1:54324", timeout=5, trust_env=False) as mail,
        TestClient(create_app(settings)) as api,
    ):
        try:
            check(api.get("/health/ready"), 200)
            check(api.get("/api/v1/auth/context"), 401)
            user = check(auth.post("/admin/users", headers=admin_api, json={
                "email": admin_mail, "password": pwd, "email_confirm": True,
            }), 200)
            with owner.begin() as db:
                bootstrap_admin(db, UUID(user["id"]))
                bootstrap_admin(db, UUID(user["id"]))

            login = check(auth.post("/token?grant_type=password", json={
                "email": admin_mail, "password": pwd,
            }), 200)
            admin_aal1 = login["access_token"]
            check(api.get("/api/v1/admin/clients", headers=bearer(admin_aal1)), 403)
            admin = bearer(activate_mfa(auth, admin_aal1, enroll_factor(auth, admin_aal1)))
            tenant_a = check(api.post("/api/v1/admin/clients", headers=admin, json={
                "name": "Empresa Teste A", "slug": slug_a,
            }), 201)["id"]
            tenant_b = check(api.post("/api/v1/admin/clients", headers=admin, json={
                "name": "Empresa Teste B", "slug": slug_b,
            }), 201)["id"]
            products = check(api.get("/api/v1/admin/products", headers=admin), 200)
            product = next(item["id"] for item in products if item["slug"] == "barbearia")
            check(api.put(f"/api/v1/admin/clients/{tenant_a}/licenses/{product}",
                          headers=admin, json={"active": True}), 200)
            invite = check(api.post(f"/api/v1/admin/clients/{tenant_a}/users", headers=admin, json={
                "email": client_mail, "role": "cliente_usuario",
            }), 201)
            assert invite["delivery"] == "sent"
            client_aal1 = check(auth.post("/verify", json={
                "type": "invite", "token_hash": mail_token(mail, client_mail, "invite"),
            }), 200)["access_token"]
            check(auth.put("/user", headers=bearer(client_aal1), json={"password": pwd}), 200)
            check(api.get("/api/v1/auth/context", headers=bearer(client_aal1)), 403)
            factor = enroll_factor(auth, client_aal1)
            client = bearer(activate_mfa(auth, client_aal1, factor))
            client_context = check(api.get("/api/v1/auth/context", headers=client), 200)
            assert client_context["organizations"] == []
            assert [item["id"] for item in client_context["invitations"]] == [invite["id"]]
            check(api.post(f"/api/v1/auth/invitations/{invite['id']}/accept", headers=admin), 404)
            check(api.post(f"/api/v1/auth/invitations/{invite['id']}/accept", headers=client), 204)
            check(api.post(f"/api/v1/auth/invitations/{invite['id']}/accept", headers=client), 204)
            granted = check(api.get(f"/api/v1/tenants/{tenant_a}/products", headers=client), 200)
            assert [item["id"] for item in granted] == [product]
            check(api.get(f"/api/v1/tenants/{tenant_b}/products", headers=client), 404)
            check(api.get("/api/v1/admin/clients", headers=client), 403)
            check(api.put(f"/api/v1/admin/clients/{tenant_b}/licenses/{product}",
                          headers=client, json={"active": True}), 403)

            existing = check(api.post(f"/api/v1/admin/clients/{tenant_b}/users", headers=admin, json={
                "email": client_mail, "role": "cliente_usuario",
            }), 201)
            assert existing["delivery"] == "existing_account"
            check(api.post(f"/api/v1/auth/invitations/{existing['id']}/accept", headers=client), 204)
            assert check(api.get(f"/api/v1/tenants/{tenant_b}/products", headers=client), 200) == []

            check(auth.post("/recover", json={"email": client_mail}), 200)
            recovery_aal1 = check(auth.post("/verify", json={
                "type": "recovery", "token_hash": mail_token(mail, client_mail, "recovery"),
            }), 200)["access_token"]
            new_pwd = secrets.token_urlsafe(24)
            rejected = auth.put("/user", headers=bearer(recovery_aal1), json={"password": new_pwd})
            check(rejected, 401)
            assert rejected.headers["x-sb-error-code"] == "insufficient_aal"
            recovery = bearer(activate_mfa(auth, recovery_aal1, factor))
            check(auth.put("/user", headers=recovery, json={"password": new_pwd}), 200)
            check(auth.post("/token?grant_type=password", json={"email": client_mail, "password": pwd}), 400)
            new_login = check(auth.post("/token?grant_type=password", json={
                "email": client_mail, "password": new_pwd,
            }), 200)
            signed_in = new_login["access_token"]
            check(api.get("/api/v1/auth/context", headers=bearer(signed_in)), 403)
            verified = bearer(activate_mfa(auth, signed_in, factor))
            check(api.get("/api/v1/auth/context", headers=verified), 200)

            route = f"/api/v1/admin/clients/{tenant_a}/users/temporary"
            change = "/api/v1/auth/first-password"
            initial = secrets.token_urlsafe(24)
            permanent = secrets.token_urlsafe(24)
            temp_inputs = {"role": "cliente_usuario", "temporary_password": initial}
            delivery_attempts = []

            class FirstAccessProbe:
                def send(self, *, frontend_url, recipient, temporary_password):
                    assert frontend_url == settings.frontend_url
                    assert temporary_password == initial
                    with owner.connect() as db:
                        assert db.scalar(text("""
                            SELECT EXISTS (
                                SELECT FROM futuranext.organization_memberships AS member
                                JOIN futuranext.profiles AS profile USING (user_id)
                                WHERE member.tenant_id = :tenant AND profile.email = :email AND member.active
                            )
                        """), {"tenant": tenant_a, "email": recipient})
                    delivery_attempts.append(recipient)
                    return recipient == temporary_mail

            api.app.state.first_access_mailer = FirstAccessProbe()
            check(api.post(route, headers=verified, json={**temp_inputs, "email": temporary_mail}), 403)
            check(api.post(route, headers=admin, json={**temp_inputs, "email": client_mail}), 409)
            assert delivery_attempts == []
            member = check(api.post(route, headers=admin, json={**temp_inputs, "email": temporary_mail}), 201)
            assert member["email"] == temporary_mail and member["active"]
            assert member["email_delivery"] == "sent"
            assert "temporary_password" not in member
            assert delivery_attempts == [temporary_mail]
            provisional = check(auth.post("/token?grant_type=password", json={
                "email": temporary_mail, "password": initial,
            }), 200)
            tmp_session = bearer(provisional["access_token"])
            assert check(api.get(change, headers=tmp_session), 200) == {"required": True}
            check(api.get("/api/v1/auth/context", headers=tmp_session), 403)
            check(api.put(change, headers=tmp_session, json={
                "temporary_password": secrets.token_urlsafe(24), "new_password": permanent,
            }), 400)
            check(api.put(change, headers=tmp_session, json={
                "temporary_password": initial, "new_password": permanent,
            }), 204)
            check(auth.post("/token?grant_type=password", json={
                "email": temporary_mail, "password": initial,
            }), 400)
            check(auth.post("/token?grant_type=refresh_token", json={
                "refresh_token": provisional["refresh_token"],
            }), 400)
            settled = check(auth.post("/token?grant_type=password", json={
                "email": temporary_mail, "password": permanent,
            }), 200)["access_token"]
            assert check(api.get(change, headers=bearer(settled)), 200) == {"required": False}
            check(api.get("/api/v1/auth/context", headers=bearer(settled)), 403)
            settled_mfa = bearer(activate_mfa(auth, settled, enroll_factor(auth, settled)))
            context = check(api.get("/api/v1/auth/context", headers=settled_mfa), 200)
            assert [item["id"] for item in context["organizations"]] == [tenant_a]

            pending = check(api.post(f"/api/v1/admin/clients/{tenant_a}/users", headers=admin, json={
                "email": pending_mail, "role": "cliente_usuario",
            }), 201)
            assert pending["delivery"] == "sent"
            opened_invite = check(auth.post("/verify", json={
                "type": "invite", "token_hash": mail_token(mail, pending_mail, "invite"),
            }), 200)
            rescued = check(api.post(route, headers=admin, json={**temp_inputs, "email": pending_mail}), 201)
            assert rescued["email"] == pending_mail and rescued["user_id"] == opened_invite["user"]["id"]
            assert rescued["email_delivery"] == "failed"
            assert delivery_attempts == [temporary_mail, pending_mail]
            users = check(api.get(f"/api/v1/admin/clients/{tenant_a}/users", headers=admin), 200)
            assert any(item["email"] == pending_mail for item in users["members"])
            assert not any(item["email"] == pending_mail for item in users["invitations"])

            deletion = f"/api/v1/admin/users/{user['id']}"
            check(api.request("DELETE", deletion, headers=admin, json={"email": admin_mail}), 409)
            check(api.post(f"/api/v1/admin/clients/{tenant_a}/users", headers=admin, json={
                "email": client_mail, "role": "cliente_usuario",
            }), 201)
            client_id = new_login["user"]["id"]
            deletion = f"/api/v1/admin/users/{client_id}"
            confirmation = {"email": client_mail}
            check(api.request("DELETE", deletion, headers=verified, json=confirmation), 403)
            check(api.request("DELETE", deletion, headers=bearer(admin_aal1), json=confirmation), 403)
            check(api.request("DELETE", deletion, headers=admin, json={"email": pending_mail}), 409)
            with owner.connect() as db:
                assert db.scalar(text("SELECT count(*) FROM futuranext.audit_logs WHERE actor_id = :id"),
                                 {"id": client_id}) > 0
            check(api.request("DELETE", deletion, headers=admin, json=confirmation), 204)
            check(api.get("/api/v1/auth/context", headers=verified), 401)
            check(auth.post("/token?grant_type=password", json={
                "email": client_mail, "password": new_pwd,
            }), 400)
            with owner.connect() as db:
                for table, column, value in (
                    ("auth.mfa_factors", "user_id", client_id),
                    ("futuranext.profiles", "user_id", client_id),
                    ("futuranext.organization_memberships", "user_id", client_id),
                    ("futuranext.audit_logs", "actor_id", client_id),
                    ("futuranext.invitations", "email", client_mail),
                ):
                    assert not db.scalar(
                        text(f"SELECT 1 FROM {table} WHERE {column} = :value LIMIT 1"), {"value": value},
                    )
            api.app.state.first_access_mailer = None
            recreated = check(api.post(route, headers=admin, json={**temp_inputs, "email": client_mail}), 201)
            assert recreated["user_id"] != client_id
            assert recreated["email_delivery"] == "unavailable"
            second_company = check(
                api.get(f"/api/v1/admin/clients/{tenant_b}/users", headers=admin), 200,
            )
            assert all(item["email"] != client_mail for item in second_company["members"])

            unconfirmed = check(auth.post("/admin/users", headers=admin_api, json={
                "email": unconfirmed_mail, "password": pwd,
            }), 200)
            search = "/api/v1/admin/access/users"
            assert check(api.get(search, headers=admin, params={"q": unconfirmed_mail}), 200) == [
                {"user_id": unconfirmed["id"], "email": unconfirmed_mail},
            ]
            assert check(api.get(f"{search}/{unconfirmed['id']}", headers=admin), 200) == []
            check(api.request("DELETE", f"/api/v1/admin/users/{unconfirmed['id']}", headers=admin,
                              json={"email": unconfirmed_mail}), 204)
            assert check(api.get(search, headers=admin, params={"q": unconfirmed_mail}), 200) == []
        finally:
            clean_test_records(
                owner, (slug_a, slug_b), (admin_mail, client_mail, temporary_mail, pending_mail, unconfirmed_mail),
            )
            owner.dispose()


def clean_test_records(owner, slugs, emails):
    with owner.begin() as db:
        tenants = db.scalars(text("SELECT id FROM futuranext.organizations WHERE slug IN (:a, :b)"),
                             {"a": slugs[0], "b": slugs[1]}).all()
        users = db.scalars(text("SELECT id FROM auth.users WHERE email = ANY(:emails)"),
                           {"emails": list(emails)}).all()
        for tenant in tenants:
            for table in ("invitations", "tenant_products", "organization_memberships"):
                db.execute(text(f"DELETE FROM futuranext.{table} WHERE tenant_id = :tenant"),
                           {"tenant": tenant})
            db.execute(text("DELETE FROM futuranext.organizations WHERE id = :tenant"), {"tenant": tenant})
            db.execute(text("DELETE FROM futuranext.audit_logs WHERE tenant_id = :tenant"), {"tenant": tenant})
        for user in users:
            db.execute(text("DELETE FROM auth.users WHERE id = :user"), {"user": user})
            db.execute(text("DELETE FROM futuranext.audit_logs WHERE actor_id = :user"), {"user": user})
