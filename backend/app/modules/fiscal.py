from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import text

from ..dependencies import CurrentUser, Data, tenant_repository
from ..repository import Repository
from .access import require_tool
from .fiscal_crypto import CertificateInputError, master_key, seal_certificate
from .fiscal_schema import (
    CertificateUpload,
    CertificateView,
    ConsultationSettings,
    EmissionSettings,
    FiscalView,
    ToolSlug,
)
from .fiscal_template import (
    CCP_TEMPLATE_FILENAME,
    TEMPLATE_FILENAME,
    TEMPLATE_MEDIA_TYPE,
    emission_template,
    legacy_ccp_template,
)

router = APIRouter(
    prefix="/api/v1/tenants/{tenant_id}/tools/{tool_slug}/fiscal",
    tags=["Ferramentas fiscais"],
)


def configured_key(request: Request) -> bytes | None:
    secret = request.app.state.settings.fiscal_master_key
    return master_key(secret.get_secret_value() if secret else None)


def get_certificate(repo: Repository, tenant_id: UUID, tool_slug: ToolSlug) -> CertificateView:
    row = (
        repo.connection.execute(
            text("""
            SELECT not_before, not_after, updated_at FROM futuranext.fiscal_certificates
            WHERE tenant_id = :tenant_id AND tool_slug = :tool_slug
        """),
            {"tenant_id": tenant_id, "tool_slug": tool_slug},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return CertificateView(status="ausente")
    now = datetime.now(UTC)
    remaining = row["not_after"] - now
    if row["not_before"] > now:
        status = "a_vigorar"
    elif remaining <= timedelta():
        status = "vencido"
    elif remaining <= timedelta(days=30):
        status = "a_vencer"
    else:
        status = "vigente"
    return CertificateView(
        status=status,
        days_remaining=max(0, ceil(remaining / timedelta(days=1))),
        **row,
    )


@router.get("", response_model=FiscalView)
def fiscal_status(
    tenant_id: UUID,
    tool_slug: ToolSlug,
    identity: CurrentUser,
    database: Data,
    request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, tool_slug)
        settings = repo.connection.scalar(
            text("""
                SELECT settings FROM futuranext.fiscal_tool_settings
                WHERE tenant_id = :tenant_id AND tool_slug = :tool_slug
            """),
            {"tenant_id": tenant_id, "tool_slug": tool_slug},
        )
        certificate = get_certificate(repo, tenant_id, tool_slug)
    return FiscalView(
        tool_slug=tool_slug,
        settings=settings,
        certificate=certificate,
        encryption_configured=configured_key(request) is not None,
        mfa_verified=identity.aal == "aal2",
    )


@router.put("/settings", response_model=EmissionSettings | ConsultationSettings)
def save_fiscal_settings(
    tenant_id: UUID,
    tool_slug: ToolSlug,
    settings: EmissionSettings | ConsultationSettings,
    identity: CurrentUser,
    database: Data,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, tool_slug, write=True)
        expected = EmissionSettings if tool_slug == "nfse" else ConsultationSettings
        if not isinstance(settings, expected):
            raise HTTPException(422, "Os campos não correspondem à ferramenta fiscal.")
        repo.connection.execute(
            text("""
                INSERT INTO futuranext.fiscal_tool_settings (tenant_id, tool_slug, settings)
                VALUES (:tenant_id, :tool_slug, CAST(:settings AS jsonb))
                ON CONFLICT (tenant_id, tool_slug) DO UPDATE SET settings = excluded.settings,
                    updated_at = now()
            """),
            {
                "tenant_id": tenant_id,
                "tool_slug": tool_slug,
                "settings": settings.model_dump_json(),
            },
        )
    return settings


def verify_fiscal_mfa(aal: str) -> None:
    if aal != "aal2":
        raise HTTPException(
            403, "Confirme a verificação em duas etapas para gerenciar o certificado."
        )


@router.put("/certificate", response_model=CertificateView)
def upload_certificate(
    tenant_id: UUID,
    tool_slug: ToolSlug,
    payload: CertificateUpload,
    identity: CurrentUser,
    database: Data,
    request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, tool_slug, write=True)
        verify_fiscal_mfa(identity.aal)
    key = configured_key(request)
    if key is None:
        raise HTTPException(503, "O armazenamento de certificados está indisponível.")
    try:
        certificate = seal_certificate(
            payload.content_base64.get_secret_value(),
            payload.password.get_secret_value(),
            key,
            tenant_id,
            tool_slug,
        )
    except CertificateInputError as exc:
        raise HTTPException(422, str(exc)) from exc

    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, tool_slug, write=True)
        # O runtime escreve o conteúdo cifrado sem ter permissão para lê-lo.
        repo.connection.execute(
            text("""
                INSERT INTO futuranext.fiscal_certificates (
                    tenant_id, tool_slug, not_before, not_after, sealed_payload
                ) VALUES (:tenant_id, :tool_slug, :not_before, :not_after, :sealed_payload)
                ON CONFLICT (tenant_id, tool_slug) DO UPDATE SET
                    not_before = :not_before, not_after = :not_after,
                    sealed_payload = :sealed_payload, updated_at = now()
            """),
            {
                "tenant_id": tenant_id,
                "tool_slug": tool_slug,
                "not_before": certificate.not_before,
                "not_after": certificate.not_after,
                "sealed_payload": certificate.sealed_payload,
            },
        )
        return get_certificate(repo, tenant_id, tool_slug)


@router.delete("/certificate", status_code=204)
def remove_certificate(
    tenant_id: UUID,
    tool_slug: ToolSlug,
    identity: CurrentUser,
    database: Data,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, tool_slug, write=True)
        verify_fiscal_mfa(identity.aal)
        repo.connection.execute(
            text("""
                DELETE FROM futuranext.fiscal_certificates
                WHERE tenant_id = :tenant_id AND tool_slug = :tool_slug
            """),
            {"tenant_id": tenant_id, "tool_slug": tool_slug},
        )
    return Response(status_code=204)


@router.get(
    "/template",
    response_class=Response,
    responses={
        200: {"content": {TEMPLATE_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def download_template(
    tenant_id: UUID,
    tool_slug: ToolSlug,
    identity: CurrentUser,
    database: Data,
    variant: Literal["generico", "ccp"] = "generico",
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_tool(repo, tenant_id, tool_slug)
        if tool_slug != "nfse":
            raise HTTPException(404, "Modelo de emissão indisponível nesta ferramenta.")
    payload, filename = (
        (legacy_ccp_template(), CCP_TEMPLATE_FILENAME)
        if variant == "ccp"
        else (emission_template(), TEMPLATE_FILENAME)
    )
    return Response(
        payload,
        media_type=TEMPLATE_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
