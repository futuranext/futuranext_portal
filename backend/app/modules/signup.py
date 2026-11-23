"""Cadastro público do site: cria a empresa com teste grátis de 15 dias e convida o administrador.

Um teste por CPF/CNPJ. A conta é ativada pelo convite enviado ao e-mail, que confirma o endereço.
"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text

from ..schemas import BillingEmail, Document, Name, Phone
from .site_chat import client_key, limiter, too_many

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/v1/public", tags=["Site"])
TRIAL_DAYS = 15


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    email: BillingEmail
    company: Name
    document: Document
    phone: Phone
    accept_terms: Literal[True]


class SignupResult(BaseModel):
    email: str
    trial_days: int
    delivery: Literal["sent", "existing_account", "pending"]


@router.post("/signup", response_model=SignupResult, status_code=201)
def signup(body: SignupRequest, request: Request) -> SignupResult:
    database = request.app.state.database
    auth = request.app.state.auth
    if database is None or auth is None:
        raise HTTPException(503, "O cadastro está indisponível no momento. Tente novamente mais tarde.")
    limits = limiter(request)
    if not limits.allow(f"signup:{client_key(request)}", 3, 3600) or not limits.allow("signup:global", 100, 86400):
        raise too_many()

    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        connection.execute(text("SELECT set_config('fn.public_signup', 'true', true)"))
        created = connection.execute(text("""
            SELECT tenant_id, invitation_id
            FROM futuranext.start_trial(:company, :document, :email, :phone, :days)
        """), {
            "company": body.company, "document": body.document, "email": body.email,
            "phone": body.phone, "days": TRIAL_DAYS,
        }).mappings().first()
    if created is None:
        raise HTTPException(
            409, "Este CPF/CNPJ já tem cadastro na FuturaNext. Entre na Área do Cliente ou fale com "
                 "suporte@futuranext.com.br.",
        )

    frontend_url = request.app.state.settings.frontend_url
    delivery = auth.send_invitation(body.email, f"{frontend_url}/ativar-conta", full_name=body.full_name)
    if delivery != "pending":
        with database.engine.begin() as connection:
            connection.execute(text("SELECT set_config('fn.public_signup', 'true', true)"))
            connection.execute(text("SELECT futuranext.record_signup_delivery(:invite, :outcome)"),
                               {"invite": created["invitation_id"], "outcome": delivery})
    else:
        logger.warning("signup_invitation_pending tenant=%s", created["tenant_id"])
    return SignupResult(email=body.email, trial_days=TRIAL_DAYS, delivery=delivery)
