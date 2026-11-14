from fastapi.testclient import TestClient

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
