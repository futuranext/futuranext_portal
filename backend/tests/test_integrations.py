import json
from datetime import UTC, datetime
from time import time
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import get_database
from app.modules import integrations
from app.modules.external_whatsapp import (
    WhatsAppEvent,
    WhatsAppWebhookError,
    send_whatsapp_event,
    sign_event,
    webhook_path,
)

SECRET = "x" * 44
INSTANCE = UUID("22222222-2222-4222-8222-222222222222")
TENANT = UUID("11111111-1111-4111-8111-111111111111")


def event() -> WhatsAppEvent:
    return WhatsAppEvent(
        id=uuid4(),
        type="message.received",
        instance_id=INSTANCE,
        occurred_at=datetime.now(UTC),
    )


def signed_headers(body: bytes, timestamp: int, secret: str = SECRET) -> dict[str, str]:
    stamp = str(timestamp)
    return {"X-Timestamp": stamp, "X-Signature": sign_event(body, stamp, secret)}


@pytest.fixture
def webhook_api(monkeypatch):
    config = {
        str(TENANT): {"whatsapp_instance_id": str(INSTANCE), "whatsapp_webhook_secret": SECRET},
    }
    monkeypatch.setenv("FN_EXTERNAL_INTEGRATIONS", json.dumps(config))
    app = FastAPI()
    app.include_router(integrations.webhook_router)
    app.dependency_overrides[get_database] = lambda: object()
    calls = []
    outcomes = iter(("accepted", "duplicate", "conflict", "unavailable"))

    def record(_database, tenant, data):
        calls.append((tenant, data))
        return next(outcomes)

    monkeypatch.setattr(integrations, "persist_event", record)
    with TestClient(app) as client:
        yield client, calls


def test_webhook_rejects_unsigned_expired_or_mismatched_events(webhook_api):
    client, calls = webhook_api
    body = event().model_dump_json().encode()
    now = int(time())
    url = webhook_path(TENANT)
    assert client.post(url, content=body).status_code == 401
    assert (
        client.post(url, content=body, headers=signed_headers(body, now - 301)).status_code == 401
    )
    assert (
        client.post(url, content=body, headers=signed_headers(body, now, "z" * 44)).status_code
        == 401
    )
    assert (
        client.post(url, content=body + b" ", headers=signed_headers(body, now)).status_code == 401
    )
    assert (
        client.post(
            webhook_path(uuid4()), content=body, headers=signed_headers(body, now)
        ).status_code
        == 401
    )

    foreign = event().model_copy(update={"instance_id": uuid4()}).model_dump_json().encode()
    assert (
        client.post(url, content=foreign, headers=signed_headers(foreign, now)).status_code == 401
    )

    body = json.dumps({**event().model_dump(mode="json"), "message": "synthetic"}).encode()
    assert client.post(url, content=body, headers=signed_headers(body, now)).status_code == 422
    assert client.post(url, content=b"x" * 4097).status_code == 413
    assert not calls


def test_webhook_reports_durable_receipts(webhook_api):
    client, calls = webhook_api
    item = event()
    body = item.model_dump_json().encode()
    headers = signed_headers(body, int(time()))
    url = webhook_path(TENANT)
    accepted = client.post(url, content=body, headers=headers)
    assert accepted.status_code == 201
    assert accepted.json() == {"status": "accepted"}
    duplicate = client.post(url, content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    assert client.post(url, content=body, headers=headers).status_code == 409
    assert client.post(url, content=body, headers=headers).status_code == 404
    assert calls == [(TENANT, item)] * 4


def test_configuration_is_tenant_scoped_and_rejects_shared_instance(monkeypatch):
    tenant_config = {
        "equinos_portal_url": "https://equinos.example.com/portal/",
        "whatsapp_portal_url": "https://whatsapp.example.com:443/",
        "whatsapp_instance_id": str(INSTANCE),
        "whatsapp_webhook_secret": SECRET,
    }
    monkeypatch.setenv("FN_EXTERNAL_INTEGRATIONS", json.dumps({str(TENANT): tenant_config}))
    own = integrations.integration_for(TENANT)
    assert own.equinos_portal_url == "https://equinos.example.com/portal"
    assert own.whatsapp_portal_url == "https://whatsapp.example.com/"
    assert SECRET not in repr(own)
    assert integrations.integration_for(uuid4()).whatsapp_instance_id is None

    shared = {
        str(TENANT): tenant_config,
        str(uuid4()): {**tenant_config, "whatsapp_portal_url": None},
    }
    monkeypatch.setenv("FN_EXTERNAL_INTEGRATIONS", json.dumps(shared))
    with pytest.raises(HTTPException) as caught:
        integrations.integration_for(TENANT)
    assert caught.value.status_code == 503
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://equinos.example.com/portal",
        "https://127.0.0.1/portal",
        "https://log:password@equinos.example.com/portal",
        "https://equinos.example.com/admin",
        "https://equinos.example.com/portal?token=synthetic",
    ],
)
def test_portal_urls_exclude_admin_paths_and_url_credentials(value):
    with pytest.raises(ValidationError):
        integrations.ExternalIntegration(equinos_portal_url=value)


def test_bot_adapter_signs_same_event_without_following_redirects():
    item = event()
    requests = []

    def deliver(request):
        requests.append(request)
        assert WhatsAppEvent.model_validate_json(request.content) == item
        expected = sign_event(request.content, request.headers["X-Timestamp"], SECRET)
        assert request.headers["X-Signature"] == expected
        assert SECRET not in str(request.headers)
        if len(requests) == 2:
            return httpx.Response(307, headers={"Location": "https://redirect.example.com/"})
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(deliver), follow_redirects=True) as client:
        url = f"https://api.example.com{webhook_path(TENANT)}"
        send_whatsapp_event(client, url, SECRET, item)
        with pytest.raises(WhatsAppWebhookError, match="HTTP 307"):
            send_whatsapp_event(client, url, SECRET, item)
    assert len(requests) == 2
