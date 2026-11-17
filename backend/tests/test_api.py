from uuid import UUID

from fastapi.testclient import TestClient

from app.auth import Identity
from app.dependencies import authenticate
from app.main import create_app
from app.settings import Settings


def test_health_and_openapi_with_no_service_configuration(tmp_path):
    settings = Settings(_env_file=None, static_dir=tmp_path, database_url=None,
                        supabase_url=None, supabase_issuer=None, supabase_publishable_key=None)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 503
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/auth/context" in paths
        assert "/api/v1/admin/clients/{tenant_id}/licenses/{product_id}" in paths
        assert "/{page}" not in paths


def test_static_files_explicit_routes_and_api_cors(tmp_path):
    (tmp_path / "index.html").write_text("home", encoding="utf-8")
    (tmp_path / "portal.html").write_text("portal", encoding="utf-8")
    (tmp_path / ".env").write_text("fake-server-secret", encoding="utf-8")
    origin = "https://portal.test"
    settings = Settings(_env_file=None, cors_origins=[origin], static_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/").text == "home"
        portal = client.get("/portal")
        assert portal.text == "portal"
        assert portal.headers["Referrer-Policy"] == "no-referrer"
        assert client.get("/.env").status_code == 404
        path = "/api/v1/auth/context"
        headers = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"}
        ok = client.options(path, headers={**headers, "Origin": origin})
        assert ok.headers["Access-Control-Allow-Origin"] == origin
        denied = client.options(path, headers={**headers, "Origin": "https://unknown.test"})
        assert denied.status_code == 400
        assert "Access-Control-Allow-Origin" not in denied.headers


def test_api_rejects_excessive_json_body():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        large = client.post("/api/v1/admin/clients", json={"name": "x" * 65536})
    assert large.status_code == 413


def test_password_session_cannot_access_private_api_routes():
    subject = UUID("d07fefb8-4bd3-4998-8c74-d1ec8e1c7122")
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[authenticate] = lambda: Identity(subject, None, "aal1", "synthetic")
    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/auth/context"),
            client.post(f"/api/v1/auth/invitations/{subject}/accept"),
            client.get(f"/api/v1/tenants/{subject}/products"),
            client.get(f"/api/v1/tenants/{subject}/tools/barbearia/agenda"),
            client.get("/api/v1/admin/clients"),
        ]
        assert all(response.status_code == 403 for response in responses)
        assert all(response.json()["detail"] == "Conclua a verificação em duas etapas."
                   for response in responses)
