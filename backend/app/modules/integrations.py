"""Acesso a portais externos dedicados e recebimento de eventos de bots."""

import os
import re
from time import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import text

from app.database import Database
from app.dependencies import CurrentUser, Data, tenant_repository

from .access import require_tool
from .external_whatsapp import (
    MAX_EVENT_BYTES,
    WEBHOOK_PATH,
    WhatsAppEvent,
    valid_signature,
)

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/tools", tags=["Integrações externas"])
webhook_router = APIRouter(tags=["Eventos externos"])

_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


def dedicated_portal_url(value: str, path: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not _HOST.fullmatch(hostname)
        or hostname.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))
        or parsed.netloc.lower() not in {hostname, f"{hostname}:443"}
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != path
        or any(char.isspace() or char == "\\" for char in value)
    ):
        raise ValueError("Use o endereço HTTPS dedicado do portal externo.")
    return f"https://{hostname}{path or '/'}"


class ExternalIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    equinos_portal_url: str | None = None
    whatsapp_portal_url: str | None = None
    whatsapp_instance_id: UUID | None = None
    whatsapp_webhook_secret: SecretStr | None = None

    @field_validator("equinos_portal_url")
    @classmethod
    def validate_equinos(cls, value: str | None) -> str | None:
        return dedicated_portal_url(value, "/portal") if value else None

    @field_validator("whatsapp_portal_url")
    @classmethod
    def validate_whatsapp(cls, value: str | None) -> str | None:
        return dedicated_portal_url(value, "") if value else None

    @model_validator(mode="after")
    def validate_webhook(self) -> "ExternalIntegration":
        if (self.whatsapp_instance_id is None) != (self.whatsapp_webhook_secret is None):
            raise ValueError("Configure a instância e o segredo do webhook juntos.")
        if self.whatsapp_webhook_secret is not None:
            secret = self.whatsapp_webhook_secret.get_secret_value()
            if len(secret.encode("utf-8")) < 32 or any(char.isspace() for char in secret):
                raise ValueError("Configure um segredo exclusivo de pelo menos 32 bytes.")
        return self


_CONFIG = TypeAdapter(dict[UUID, ExternalIntegration])


def integration_for(tenant_id: UUID) -> ExternalIntegration:
    raw = os.environ.get("FN_EXTERNAL_INTEGRATIONS")
    if not raw:
        return ExternalIntegration()
    try:
        configs = _CONFIG.validate_json(raw)
        for field in ("equinos_portal_url", "whatsapp_portal_url", "whatsapp_instance_id"):
            values = [
                getattr(config, field) for config in configs.values() if getattr(config, field)
            ]
            if len(set(values)) != len(values):
                raise ValueError("Cada empresa precisa de uma integração dedicada.")
        secrets = [
            config.whatsapp_webhook_secret.get_secret_value()
            for config in configs.values()
            if config.whatsapp_webhook_secret is not None
        ]
        if len(set(secrets)) != len(secrets):
            raise ValueError("Cada empresa precisa de um segredo dedicado.")
    except (ValidationError, ValueError):
        raise HTTPException(
            503, "A configuração das integrações externas precisa de revisão."
        ) from None
    return configs.get(tenant_id, ExternalIntegration())


class PortalAccess(BaseModel):
    portal_url: str | None = None


@router.get("/cotacao-seguros", response_model=PortalAccess)
def equinos_portal(tenant_id: UUID, identity: CurrentUser, database: Data) -> PortalAccess:
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, "cotacao-seguros")
        return PortalAccess(portal_url=integration_for(tenant_id).equinos_portal_url)


def persist_event(database: Database, tenant_id: UUID, event: WhatsAppEvent) -> str:
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        return connection.scalar(
            text("""
                SELECT futuranext.register_whatsapp_event(
                    :tenant_id, :instance_id, :event_id, :event_kind, :event_time
                )
            """),
            {
                "tenant_id": tenant_id,
                "instance_id": event.instance_id,
                "event_id": event.id,
                "event_kind": event.type,
                "event_time": event.occurred_at,
            },
        )


class EventReceipt(BaseModel):
    status: Literal["accepted", "duplicate"]


@webhook_router.post(
    WEBHOOK_PATH,
    response_model=EventReceipt,
    status_code=201,
    responses={200: {"model": EventReceipt}},
)
async def receive_whatsapp_event(tenant_id: UUID, request: Request, database: Data) -> JSONResponse:
    config = integration_for(tenant_id)
    secret = config.whatsapp_webhook_secret
    auth_error = HTTPException(401, "Webhook não autorizado.")
    if secret is None:
        raise auth_error
    body = await request.body()
    if len(body) > MAX_EVENT_BYTES:
        raise HTTPException(413, "O evento excede o limite permitido.")
    if not valid_signature(
        body,
        request.headers.get("x-timestamp", ""),
        request.headers.get("x-signature", ""),
        secret.get_secret_value(),
        int(time()),
    ):
        raise auth_error
    try:
        event = WhatsAppEvent.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Confira os metadados do evento.") from None
    if event.instance_id != config.whatsapp_instance_id:
        raise auth_error
    result = await run_in_threadpool(persist_event, database, tenant_id, event)
    if result == "unavailable":
        raise HTTPException(404, "Webhook indisponível.")
    if result == "conflict":
        raise HTTPException(409, "O identificador já corresponde a outro evento.")
    if result == "duplicate":
        return JSONResponse({"status": "duplicate"}, status_code=200)
    if result == "accepted":
        return JSONResponse({"status": "accepted"}, status_code=201)
    raise HTTPException(503, "O registro do evento está indisponível.")
