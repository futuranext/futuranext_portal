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
        finally:
            clean_test_records(owner, (slug_a, slug_b), (admin_mail, client_mail))
            owner.dispose()


def clean_test_records(owner, slugs, emails):
    with owner.begin() as db:
        tenants = db.scalars(text("SELECT id FROM futuranext.organizations WHERE slug IN (:a, :b)"),
                             {"a": slugs[0], "b": slugs[1]}).all()
        users = db.scalars(text("SELECT id FROM auth.users WHERE email IN (:a, :b)"),
                           {"a": emails[0], "b": emails[1]}).all()
        for tenant in tenants:
            for table in ("invitations", "tenant_products", "organization_memberships"):
                db.execute(text(f"DELETE FROM futuranext.{table} WHERE tenant_id = :tenant"),
                           {"tenant": tenant})
            db.execute(text("DELETE FROM futuranext.organizations WHERE id = :tenant"), {"tenant": tenant})
            db.execute(text("DELETE FROM futuranext.audit_logs WHERE tenant_id = :tenant"), {"tenant": tenant})
        for user in users:
            db.execute(text("DELETE FROM auth.users WHERE id = :user"), {"user": user})
            db.execute(text("DELETE FROM futuranext.audit_logs WHERE actor_id = :user"), {"user": user})
