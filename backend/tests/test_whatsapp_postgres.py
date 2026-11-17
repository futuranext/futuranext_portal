import re
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_postgres import Platform, platform  # noqa: F401 - fixture PostgreSQL compartilhada

from app.auth import Identity
from app.dependencies import authenticate, get_database
from app.modules.whatsapp import router
from app.repository import Repository


class FakeService:
    def __init__(self):
        self.calls = []
        self.state = {"instance": None, "webhook": None, "keys": []}

    def request(self, method, tenant_id, path="", *, actor=None, body=None, timeout=10):
        self.calls.append((method, path, body))
        if (method, path) == ("POST", "/instance"):
            self.state["instance"] = {"id": "i-1", "connection_state": "disconnected"}
        if path == "/qr":
            return {"base64": "data:image/png;base64,AAAA", "expires_at": None}
        if (method, path) == ("PUT", "/webhook"):
            self.state["webhook"] = {"url": body["url"], "include_message": True}
            return {**self.state, "webhook_secret": "whsec_once"}
        return dict(self.state)


class FakeMailer:
    def __init__(self, delivered=True):
        self.messages, self.delivered = [], delivered

    def deliver(self, *, recipient, subject, html):
        self.messages.append((recipient, html))
        return self.delivered


def build(platform: Platform, user, email, *, mailer=None, service=None):  # noqa: F811
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(
        frontend_url="https://futuranext.example", whatsapp_public_api_url="https://api.example/whatsapp",
    )
    app.state.whatsapp = service or FakeService()
    app.state.first_access_mailer = mailer or FakeMailer()
    app.dependency_overrides[get_database] = lambda: platform.database
    app.dependency_overrides[authenticate] = lambda: Identity(user, email, "aal2", "test")
    return app


def license_whatsapp(platform: Platform) -> None:  # noqa: F811
    with platform.owner.begin() as db:
        product = db.scalar(text("SELECT id FROM futuranext.products WHERE slug = 'whatsapp'"))
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as db:
        Repository(db).upsert_entitlement(platform.tenant_a, product, True, None, None)


def test_consent_by_email_gates_pairing_for_the_same_user(platform: Platform) -> None:  # noqa: F811
    license_whatsapp(platform)
    service, mailer = FakeService(), FakeMailer()
    app = build(platform, platform.alice, "alice@example.test", mailer=mailer, service=service)
    path = f"/api/v1/tenants/{platform.tenant_a}/tools/whatsapp"

    with TestClient(app) as client:
        status = client.get(path).json()
        assert status["can_manage"] and status["consent"]["status"] == "none"
        assert client.post(f"{path}/connect").status_code == 409
        assert client.get(f"{path}/qr").status_code == 409
        assert service.calls == [("GET", "", None)]

        requested = client.post(f"{path}/consent")
        assert requested.status_code == 200 and requested.json()["consent"]["status"] == "pending"
        recipient, html = mailer.messages[0]
        assert recipient == "alice@example.test"
        token = re.search(r"#aceite=([A-Za-z0-9_-]+)", html)[1]
        assert f"empresa={platform.tenant_a}" in html
        assert client.post(f"{path}/consent").status_code == 429

        assert client.post(f"{path}/consent/confirm", json={"token": "x" * 43}).status_code == 422
        confirmed = client.post(f"{path}/consent/confirm", json={"token": token})
        assert confirmed.json()["consent"]["status"] == "accepted"
        assert client.post(f"{path}/consent/confirm", json={"token": token}).status_code == 200

        connected = client.post(f"{path}/connect")
        assert connected.status_code == 200
        assert connected.json()["instance"]["connection_state"] == "disconnected"
        assert any(call[:2] == ("POST", "/instance") for call in service.calls)
        assert ("POST", "/connect", None) in service.calls
        assert client.get(f"{path}/qr").json()["base64"].startswith("data:image/png")

        saved = client.put(f"{path}/webhook", json={"url": "https://bot.example.com/hook"})
        assert saved.json()["webhook_secret"] == "whsec_once"
        assert "whsec_once" not in client.get(path).text

    with platform.database.session(platform.alice, "aal2", platform.tenant_a) as db:
        row = db.execute(text("""
            SELECT token_hash, accepted_at FROM futuranext.whatsapp_consents
        """)).one()
        assert row.token_hash is None and row.accepted_at is not None
    with platform.database.session(platform.bob, "aal2", platform.tenant_b) as db:
        assert db.execute(text("SELECT count(*) FROM futuranext.whatsapp_consents")).scalar() == 0


def test_consent_is_not_sent_when_email_fails(platform: Platform) -> None:  # noqa: F811
    license_whatsapp(platform)
    with platform.owner.begin() as db:
        db.execute(text("DELETE FROM futuranext.whatsapp_consents"))
    app = build(platform, platform.alice, "alice@example.test", mailer=FakeMailer(delivered=False))
    path = f"/api/v1/tenants/{platform.tenant_a}/tools/whatsapp"
    with TestClient(app) as client:
        assert client.post(f"{path}/consent").status_code == 503
        # A falha desfaz o registro, então um novo pedido não espera o intervalo.
        assert client.get(path).json()["consent"]["status"] == "none"


def test_whatsapp_requires_license_and_admin(platform: Platform) -> None:  # noqa: F811
    path_b = f"/api/v1/tenants/{platform.tenant_b}/tools/whatsapp"
    with TestClient(build(platform, platform.bob, "bob@example.test")) as client:
        assert client.get(path_b).status_code == 404
        assert client.post(f"{path_b}/consent").status_code == 404
    path_a = f"/api/v1/tenants/{platform.tenant_a}/tools/whatsapp"
    with TestClient(build(platform, platform.bob, "bob@example.test")) as client:
        assert client.get(path_a).status_code in {403, 404}
