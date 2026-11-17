"""Contrato do webhook de metadados que os bots podem importar diretamente."""

import hmac
import re
from time import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict

SIGNATURE_WINDOW_SECONDS = 300
MAX_EVENT_BYTES = 4096
WEBHOOK_PATH = "/api/v1/integrations/whatsapp/{tenant_id}/events"


class WhatsAppEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: UUID
    type: Literal["message.received", "message.status", "instance.connection"]
    instance_id: UUID
    occurred_at: AwareDatetime


def webhook_path(tenant_id: UUID) -> str:
    return WEBHOOK_PATH.format(tenant_id=tenant_id)


def sign_event(body: bytes, timestamp: str, secret: str) -> str:
    message = timestamp.encode("ascii") + b"." + body
    digest = hmac.digest(secret.encode("utf-8"), message, "sha256")
    return f"sha256={digest.hex()}"


def valid_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    now: int,
) -> bool:
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or not re.fullmatch(
        r"sha256=[a-f0-9]{64}",
        signature,
    ):
        return False
    if abs(now - int(timestamp)) > SIGNATURE_WINDOW_SECONDS:
        return False
    return hmac.compare_digest(sign_event(body, timestamp, secret), signature)


class WhatsAppWebhookError(Exception):
    """O portal não confirmou o recebimento de metadados."""


def send_whatsapp_event(
    client: httpx.Client,
    endpoint: str,
    secret: str,
    event: WhatsAppEvent,
) -> None:
    """Registra um evento no portal. Reutilize o ID se precisar repetir a ação."""
    url = urlsplit(endpoint)
    local = url.hostname in {"127.0.0.1", "::1", "localhost"}
    if (
        (url.scheme != "https" and not (url.scheme == "http" and local))
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
        or not re.fullmatch(r"/api/v1/integrations/whatsapp/[0-9a-f-]{36}/events", url.path)
        or any(char.isspace() or char == "\\" for char in endpoint)
        or len(secret.encode("utf-8")) < 32
    ):
        raise WhatsAppWebhookError(
            "Configure a URL do webhook e um segredo de pelo menos 32 bytes."
        )

    body = event.model_dump_json().encode("utf-8")
    timestamp = str(int(time()))
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Signature": sign_event(body, timestamp, secret),
    }
    try:
        with client.stream(
            "POST",
            endpoint,
            content=body,
            headers=headers,
            timeout=5,
            follow_redirects=False,
        ) as response:
            if response.status_code not in {200, 201}:
                raise WhatsAppWebhookError(f"Webhook não confirmado (HTTP {response.status_code}).")
    except httpx.RequestError:
        raise WhatsAppWebhookError("Falha de conexão com o webhook do portal.") from None
