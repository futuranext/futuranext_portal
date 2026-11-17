from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.modules.whatsapp import WhatsAppService, consent_html, consent_link
from app.settings import Settings

TOKEN = "t" * 40


def make(handler):
    return WhatsAppService(httpx.Client(transport=httpx.MockTransport(handler)), "http://whatsapp-api:8000", TOKEN)


def test_service_sends_token_actor_and_tenant_path():
    seen = {}
    tenant, actor = uuid4(), uuid4()

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers["authorization"],
                    actor=request.headers["x-actor-id"])
        return httpx.Response(200, json={"instance": None})

    assert make(handler).request("GET", tenant, "/qr", actor=actor) == {"instance": None}
    assert seen == {"url": f"http://whatsapp-api:8000/service/v1/tenants/{tenant}/whatsapp/qr",
                    "auth": f"Bearer {TOKEN}", "actor": str(actor)}


@pytest.mark.parametrize(("status", "expected"), [(409, 409), (422, 422), (401, 503), (500, 503)])
def test_service_errors_are_translated(status, expected):
    def handler(request):
        return httpx.Response(status, json={"error": {"code": "X", "message": "Mensagem do serviço."}})

    with pytest.raises(HTTPException) as error:
        make(handler).request("POST", uuid4(), "/connect")
    assert error.value.status_code == expected
    if expected != 503:
        assert error.value.detail == "Mensagem do serviço."


def test_network_failure_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("offline")

    with pytest.raises(HTTPException) as error:
        make(handler).request("GET", uuid4())
    assert error.value.status_code == 503


def test_consent_email_escapes_company_and_keeps_token_in_fragment():
    tenant = uuid4()
    link = consent_link("https://futuranext.com.br/", tenant, "abc_DEF-123")
    assert link == (f"https://futuranext.com.br/portal/produtos?ferramenta=whatsapp&empresa={tenant}"
                    "#aceite=abc_DEF-123")
    html = consent_html(link, "<Empresa & Cia>", "pessoa@example.com")
    assert "&lt;Empresa &amp; Cia&gt;" in html and "<Empresa" not in html
    assert "SAIR, PARAR" in html


def test_settings_require_service_url_and_token_together():
    with pytest.raises(ValueError):
        Settings(_env_file=None, whatsapp_service_url="http://whatsapp-api:8000")
    with pytest.raises(ValueError):
        Settings(_env_file=None, whatsapp_service_url="http://whatsapp-api:8000",
                 whatsapp_service_token="short")
    config = Settings(_env_file=None, whatsapp_service_url="http://whatsapp-api:8000",
                      whatsapp_service_token=TOKEN,
                      whatsapp_public_api_url="https://api.futuranext.com.br/whatsapp")
    assert config.whatsapp_public_api_url == "https://api.futuranext.com.br/whatsapp"
