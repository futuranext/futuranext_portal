"""Ferramenta WhatsApp do portal: aceite por e-mail, pareamento, webhook do bot e chaves de API.

O serviço WhatsApp (pasta whatsapp/) roda na rede interna do Droplet. O portal autentica o
usuário, confere a licença e o aceite e então chama o serviço com um token exclusivo.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from html import escape
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.dependencies import CurrentUser, Data, tenant_repository
from app.repository import Repository

from .access import require_tool

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/tools/whatsapp", tags=["WhatsApp"])

TERMS_VERSION = "2026-11-17"
CONSENT_TTL = timedelta(hours=24)
CONSENT_RESEND = timedelta(minutes=2)
SUBJECT = "Confirme o uso do WhatsApp na FuturaNext"
UNAVAILABLE = "O serviço de WhatsApp está indisponível. Tente novamente em instantes."
TERMS = (
    "Vou conectar um número de WhatsApp que pertence à empresa ou que tenho autorização para usar.",
    (
        "A conexão usa o WhatsApp Web por meio de um gateway não oficial. O WhatsApp pode "
        "restringir, desconectar ou bloquear o número a qualquer momento, sem garantia de entrega."
    ),
    (
        "Só enviarei mensagens a pessoas que se relacionam com a empresa, sem spam nem envio em "
        "massa não solicitado, respeitando a LGPD e os termos do WhatsApp."
    ),
    "Quem responder SAIR, PARAR, STOP ou CANCELAR deixa de receber mensagens automaticamente.",
    (
        "As chaves de API e o segredo do webhook são de responsabilidade da empresa e devem "
        "ficar guardados apenas no código do bot."
    ),
)


# --- Cliente do serviço interno -------------------------------------------------------------


class WhatsAppService:
    def __init__(self, client: httpx.Client, base_url: str, token: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._token = token

    def request(
        self, method: str, tenant_id: UUID, path: str = "", *,
        actor: UUID | None = None, body: dict[str, Any] | None = None, timeout: float = 10,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if actor:
            headers["X-Actor-Id"] = str(actor)
        try:
            response = self._client.request(
                method, f"{self._base}/service/v1/tenants/{tenant_id}/whatsapp{path}",
                json=body, headers=headers, timeout=timeout, follow_redirects=False,
            )
        except httpx.RequestError:
            raise HTTPException(503, UNAVAILABLE) from None
        if response.status_code in {404, 409, 422, 429}:
            raise HTTPException(response.status_code, _service_message(response))
        if not response.is_success:
            raise HTTPException(503, UNAVAILABLE)
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(503, UNAVAILABLE) from None
        if not isinstance(payload, dict):
            raise HTTPException(503, UNAVAILABLE)
        return payload


def _service_message(response: httpx.Response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = None
    if isinstance(message, str) and 0 < len(message) <= 300:
        return message
    return "Não foi possível concluir a ação no WhatsApp."


def service(request: Request) -> WhatsAppService:
    client = getattr(request.app.state, "whatsapp", None)
    if client is None:
        raise HTTPException(503, "A ferramenta WhatsApp ainda não foi configurada pela FuturaNext.")
    return client


# --- Modelos ----------------------------------------------------------------------------------


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Instance(Model):
    id: str
    connection_state: str
    phone_masked: str | None = None
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_error: str | None = None


class Webhook(Model):
    url: str
    include_message: bool
    updated_at: datetime | None = None


class ApiKey(Model):
    id: str
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime


class Consent(BaseModel):
    status: Literal["accepted", "pending", "expired", "none"]
    email: str | None = None
    requested_at: datetime | None = None
    expires_at: datetime | None = None
    accepted_at: datetime | None = None


class WhatsAppStatus(BaseModel):
    configured: bool
    can_manage: bool
    terms_version: str = TERMS_VERSION
    terms: tuple[str, ...] = TERMS
    consent: Consent
    api_base_url: str | None = None
    instance: Instance | None = None
    webhook: Webhook | None = None
    keys: list[ApiKey] = []
    webhook_secret: str | None = None
    api_key: str | None = None


class ConsentConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


class WebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=12, max_length=2048)
    include_message: bool = True
    rotate_secret: bool = False


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


# --- Aceite -----------------------------------------------------------------------------------


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def consent_row(repo: Repository, tenant_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    row = repo.connection.execute(text("""
        SELECT email, terms_version, token_hash, requested_at, expires_at, accepted_at, now() AS now
        FROM futuranext.whatsapp_consents WHERE tenant_id = :tenant_id AND user_id = :user_id
    """), {"tenant_id": tenant_id, "user_id": user_id}).mappings().one_or_none()
    return dict(row) if row else None


def consent_state(row: dict[str, Any] | None) -> Consent:
    if row is None:
        return Consent(status="none")
    if row["accepted_at"] and row["terms_version"] == TERMS_VERSION:
        status = "accepted"
    elif row["token_hash"] and row["expires_at"] > row["now"]:
        status = "pending"
    elif row["token_hash"]:
        status = "expired"
    else:
        status = "none"
    return Consent(
        status=status, email=row["email"], requested_at=row["requested_at"],
        expires_at=row["expires_at"] if status in {"pending", "expired"} else None,
        accepted_at=row["accepted_at"] if status == "accepted" else None,
    )


def consent_link(frontend_url: str, tenant_id: UUID, token: str) -> str:
    return (f"{frontend_url.rstrip('/')}/portal/produtos?ferramenta=whatsapp"
            f"&empresa={tenant_id}#aceite={token}")


def consent_html(link: str, company: str, recipient: str) -> str:
    items = "".join(f'<li style="margin:0 0 8px;">{escape(item)}</li>' for item in TERMS)
    return f"""<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SUBJECT}</title></head>
<body style="margin:0;background:#f3f7f3;color:#102838;font-family:Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f3f7f3">
    <tr><td align="center" style="padding:24px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
        style="max-width:560px;border:1px solid #d8e3de;border-radius:12px;">
        <tr><td bgcolor="#081a2a"
          style="padding:20px 24px;border-radius:11px 11px 0 0;color:#fff;font-size:22px;font-weight:bold;">
          Futura<span style="color:#08ce88;">Next</span>
        </td></tr>
        <tr><td style="padding:28px 24px 30px;font-size:16px;line-height:1.55;">
          <h1 style="margin:0 0 16px;font-size:24px;line-height:1.25;">Confirme o uso do WhatsApp</h1>
          <p style="margin:0 0 16px;">Recebemos um pedido para conectar o WhatsApp da empresa
            <strong>{escape(company)}</strong> com a conta <strong>{escape(recipient)}</strong>.
            Ao confirmar, você declara que:</p>
          <ul style="margin:0 0 20px;padding-left:20px;">{items}</ul>
          <table role="presentation" cellpadding="0" cellspacing="0">
            <tr><td bgcolor="#08ce88" style="border-radius:8px;mso-padding-alt:14px 22px;">
              <a href="{escape(link)}" style="display:inline-block;padding:14px 22px;color:#062d22;
                font-weight:bold;line-height:1.25;text-decoration:none;">Li e aceito</a>
            </td></tr>
          </table>
          <p style="margin:20px 0 0;">O link vale por 24 horas e só funciona com você conectado
            ao portal. Se não foi você, ignore este e-mail.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# --- Regras comuns ----------------------------------------------------------------------------


def company_name(repo: Repository, tenant_id: UUID) -> str:
    name = repo.connection.scalar(
        text("SELECT name FROM futuranext.organizations WHERE id = :id"), {"id": tenant_id},
    )
    return name or "Empresa"


def authorize(
    database, identity, tenant_id: UUID, *, write: bool = False, accepted: bool = False,
) -> tuple[str, Consent, bool]:
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, "whatsapp", write=write)
        can_manage = repo.can_use_tool(tenant_id, "whatsapp", write=True)
        consent = consent_state(consent_row(repo, tenant_id, identity.user_id)) if can_manage \
            else Consent(status="none")
        if accepted and consent.status != "accepted":
            raise HTTPException(
                409, "Confirme o aceite enviado ao seu e-mail antes de conectar o WhatsApp.",
            )
        return company_name(repo, tenant_id), consent, can_manage


def status_response(
    request: Request, consent: Consent, can_manage: bool, payload: dict[str, Any] | None,
) -> WhatsAppStatus:
    payload = payload or {}
    settings = request.app.state.settings
    return WhatsAppStatus(
        configured=getattr(request.app.state, "whatsapp", None) is not None,
        can_manage=can_manage,
        consent=consent,
        api_base_url=settings.whatsapp_public_api_url,
        instance=payload.get("instance"),
        webhook=payload.get("webhook") if can_manage else None,
        keys=(payload.get("keys") or []) if can_manage else [],
        webhook_secret=payload.get("webhook_secret"),
        api_key=payload.get("api_key"),
    )


def managed(
    request: Request, tenant_id: UUID, identity, database, method: str, path: str,
    body: dict[str, Any] | None = None, timeout: float = 10,
) -> WhatsAppStatus:
    client = service(request)
    _, consent, can_manage = authorize(database, identity, tenant_id, write=True, accepted=True)
    payload = client.request(method, tenant_id, path, actor=identity.user_id, body=body,
                             timeout=timeout)
    return status_response(request, consent, can_manage, payload)


# --- Rotas ------------------------------------------------------------------------------------


@router.get("", response_model=WhatsAppStatus)
def get_status(tenant_id: UUID, request: Request, identity: CurrentUser, database: Data):
    _, consent, can_manage = authorize(database, identity, tenant_id)
    client = getattr(request.app.state, "whatsapp", None)
    payload = None
    if client is not None:
        try:
            payload = client.request("GET", tenant_id, timeout=8)
        except HTTPException as error:
            if error.status_code != 503:
                raise
    return status_response(request, consent, can_manage, payload)


@router.post("/consent", response_model=WhatsAppStatus)
def request_consent(tenant_id: UUID, request: Request, identity: CurrentUser, database: Data):
    service(request)
    mailer = request.app.state.first_access_mailer
    if mailer is None:
        raise HTTPException(503, "O envio de e-mails do portal ainda não foi configurado.")
    if not identity.email:
        raise HTTPException(409, "Sua conta precisa de um e-mail confirmado para o aceite.")

    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, "whatsapp", write=True)
        current = consent_row(repo, tenant_id, identity.user_id)
        state = consent_state(current)
        if state.status == "accepted":
            return status_response(request, state, True, None)
        if current and current["token_hash"] and current["requested_at"] > current["now"] - CONSENT_RESEND:
            raise HTTPException(429, "Aguarde dois minutos para pedir um novo e-mail de aceite.")
        token = secrets.token_urlsafe(32)
        repo.connection.execute(text("""
            INSERT INTO futuranext.whatsapp_consents AS consent
                (tenant_id, user_id, email, terms_version, token_hash, requested_at, expires_at)
            VALUES (:tenant_id, :user_id, :email, :terms, :hash, now(), now() + :ttl)
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                email = EXCLUDED.email, terms_version = EXCLUDED.terms_version,
                token_hash = EXCLUDED.token_hash, requested_at = EXCLUDED.requested_at,
                expires_at = EXCLUDED.expires_at, accepted_at = NULL
        """), {
            "tenant_id": tenant_id, "user_id": identity.user_id, "email": identity.email.lower(),
            "terms": TERMS_VERSION, "hash": token_hash(token), "ttl": CONSENT_TTL,
        })
        company = company_name(repo, tenant_id)
        link = consent_link(request.app.state.settings.frontend_url, tenant_id, token)
        # Falha no envio desfaz o registro: o usuário pode pedir de novo sem esperar.
        if not mailer.deliver(recipient=identity.email, subject=SUBJECT,
                              html=consent_html(link, company, identity.email)):
            raise HTTPException(503, "Não foi possível enviar o e-mail de aceite. Tente novamente.")
        state = consent_state(consent_row(repo, tenant_id, identity.user_id))
    return status_response(request, state, True, None)


@router.post("/consent/confirm", response_model=WhatsAppStatus)
def confirm_consent(
    tenant_id: UUID, body: ConsentConfirmation, request: Request,
    identity: CurrentUser, database: Data,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, "whatsapp", write=True)
        current = consent_row(repo, tenant_id, identity.user_id)
        if consent_state(current).status == "accepted":
            return status_response(request, consent_state(current), True, None)
        invalid = HTTPException(
            422, "Este link de aceite não vale para a sua conta. Solicite um novo e-mail.",
        )
        if not current or not current["token_hash"]:
            raise invalid
        if not hmac.compare_digest(current["token_hash"], token_hash(body.token)):
            raise invalid
        if current["expires_at"] <= current["now"]:
            raise HTTPException(410, "O link de aceite expirou. Solicite um novo e-mail.")
        repo.connection.execute(text("""
            UPDATE futuranext.whatsapp_consents SET accepted_at = now(), token_hash = NULL
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND token_hash = :hash
        """), {"tenant_id": tenant_id, "user_id": identity.user_id, "hash": current["token_hash"]})
        state = consent_state(consent_row(repo, tenant_id, identity.user_id))
    return status_response(request, state, True, None)


@router.post("/connect", response_model=WhatsAppStatus)
def connect(tenant_id: UUID, request: Request, identity: CurrentUser, database: Data):
    client = service(request)
    company, consent, can_manage = authorize(
        database, identity, tenant_id, write=True, accepted=True,
    )
    payload = client.request("POST", tenant_id, "/instance", actor=identity.user_id,
                             body={"company_name": company}, timeout=30)
    if (payload.get("instance") or {}).get("connection_state") != "connected":
        client.request("POST", tenant_id, "/connect", actor=identity.user_id, timeout=30)
    return status_response(request, consent, can_manage, client.request("GET", tenant_id))


@router.get("/qr")
def qr_code(tenant_id: UUID, request: Request, identity: CurrentUser, database: Data):
    client = service(request)
    authorize(database, identity, tenant_id, write=True, accepted=True)
    payload = client.request("GET", tenant_id, "/qr", actor=identity.user_id)
    image = payload.get("base64")
    if not isinstance(image, str) or not image.startswith("data:image/png;base64,"):
        raise HTTPException(409, "O QR Code ainda não está disponível.")
    return {"base64": image, "expires_at": payload.get("expires_at")}


@router.post("/disconnect", response_model=WhatsAppStatus)
def disconnect(tenant_id: UUID, request: Request, identity: CurrentUser, database: Data):
    return managed(request, tenant_id, identity, database, "POST", "/disconnect", timeout=20)


@router.put("/webhook", response_model=WhatsAppStatus)
def save_webhook(
    tenant_id: UUID, body: WebhookInput, request: Request, identity: CurrentUser, database: Data,
):
    return managed(request, tenant_id, identity, database, "PUT", "/webhook", body.model_dump())


@router.delete("/webhook", response_model=WhatsAppStatus)
def delete_webhook(tenant_id: UUID, request: Request, identity: CurrentUser, database: Data):
    return managed(request, tenant_id, identity, database, "DELETE", "/webhook")


@router.post("/keys", response_model=WhatsAppStatus, status_code=201)
def create_key(
    tenant_id: UUID, body: KeyInput, request: Request, identity: CurrentUser, database: Data,
):
    return managed(request, tenant_id, identity, database, "POST", "/keys", body.model_dump())


@router.delete("/keys/{key_id}", response_model=WhatsAppStatus)
def revoke_key(
    tenant_id: UUID, key_id: UUID, request: Request, identity: CurrentUser, database: Data,
):
    return managed(request, tenant_id, identity, database, "DELETE", f"/keys/{key_id}")
