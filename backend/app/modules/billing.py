from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import text

from ..dependencies import CurrentUser, Data, require_permission, tenant_repository
from ..repository import Record
from ..schemas import BillingCustomerStatus, Input, Name
from .asaas import (
    AsaasCharge,
    AsaasClient,
    AsaasError,
    AsaasRejected,
    ChargeRequest,
    CustomerRequest,
)
from .billing_repository import BillingRepository

router = APIRouter(prefix="/api/v1", tags=["Cobranças"])
BillingEnvironment = Literal["sandbox", "production"]
InvoiceStatus = Literal[
    "open", "paid", "confirmed", "overdue", "processing", "refunded", "cancelled", "review",
]
CUSTOMER_PATTERN = r"^cus_[a-zA-Z0-9_-]{1,120}$"
REFERENCE_PREFIX = "futuranext:invoice:"
CUSTOMER_REFERENCE_PREFIX = "futuranext:tenant:"
CUSTOMER_MESSAGES: dict[str, str] = {
    "linked": "Cliente Asaas vinculado.",
    "incomplete": "Complete CPF/CNPJ, e-mail e telefone no cadastro da empresa.",
    "no_access": "O cliente Asaas será criado quando a empresa tiver um usuário com acesso ativo.",
    "inactive": "Reative a empresa para criar o cliente Asaas.",
    "unavailable": "Integração Asaas não configurada neste ambiente.",
    "rejected": "O Asaas recusou os dados do cliente. Confira CPF/CNPJ, e-mail e telefone.",
    "failed": "Não foi possível criar o cliente no Asaas agora. Tente novamente em instantes.",
    "busy": "O cliente Asaas está sendo criado. Atualize em instantes.",
}
STATUS = {
    "PENDING": "open",
    "RECEIVED": "paid",
    "CONFIRMED": "confirmed",
    "RECEIVED_IN_CASH": "paid",
    "OVERDUE": "overdue",
    "REFUNDED": "refunded",
    "DELETED": "cancelled",
    "AWAITING_RISK_ANALYSIS": "processing",
}


class AccountInput(Input):
    customer_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=CUSTOMER_PATTERN)]


class Account(AccountInput):
    environment: BillingEnvironment


class ServiceInput(Input):
    name: Name
    amount_cents: Annotated[int, Field(strict=True, ge=1, le=10_000_000)]
    active: bool


class Service(ServiceInput):
    id: UUID


class IssueInvoice(Input):
    period: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
    due_date: date

    @field_validator("period")
    @classmethod
    def validate_calendar(cls, value: str) -> str:
        date.fromisoformat(value + "-01")
        return value


class InvoiceLine(BaseModel):
    name: str
    amount_cents: int


class Invoice(BaseModel):
    id: UUID
    period: str
    due_date: date
    total_cents: int
    status: InvoiceStatus
    items: list[InvoiceLine]
    payment_url: str | None
    created_at: AwareDatetime
    can_cancel: bool = False
    can_reconcile: bool = False
    reference: str | None = None


class PortalBilling(BaseModel):
    payment_available: bool
    customer_ready: bool = False
    invoices: list[Invoice]


class AdminBilling(PortalBilling):
    environment: BillingEnvironment
    account: Account | None
    customer_status: BillingCustomerStatus
    customer_message: str
    services: list[Service]
    webhook_configured: bool


class EventPayment(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^pay_[a-zA-Z0-9_-]{1,120}$")]
    externalReference: Annotated[str, StringConstraints(max_length=200)] | None = None


class PaymentEvent(BaseModel):
    id: Annotated[str, StringConstraints(min_length=1, max_length=180)]
    event: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    payment: EventPayment | None = None


def today() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def asaas_client(request: Request) -> AsaasClient | None:
    return request.app.state.asaas


Asaas = Annotated[AsaasClient | None, Depends(asaas_client)]


def environment(request: Request) -> BillingEnvironment:
    return request.app.state.settings.asaas_environment


def invoice_view(invoice: Record, mode: str, *, admin: bool = False) -> Invoice:
    status = invoice["status"]
    if status == "open" and invoice["due_date"] < today():
        status = "overdue"
    return Invoice.model_validate({
        **invoice, "period": invoice["period"].strftime("%Y-%m"), "status": status,
        "can_cancel": admin and can_cancel_invoice(invoice),
        "can_reconcile": admin and invoice["environment"] == mode and invoice["provider_state"] in {
            "inflight", "uncertain", "linked",
        },
        "reference": reference(invoice["id"]) if admin else None,
        "payment_url": invoice["payment_url"] if invoice["environment"] == mode else None,
    })


def can_cancel_invoice(invoice: Record) -> bool:
    return (
        invoice["status"] != "cancelled" and not invoice["provider_id"]
        and invoice["provider_state"] in {"unsubmitted", "rejected"}
    )


def found(record: Record | None) -> Record:
    if record is None:
        raise HTTPException(404, "Registro de cobrança indisponível.")
    return record


def billing_admin(repo: BillingRepository, tenant_id: UUID) -> None:
    require_permission(repo, "admin.entitlement.manage")
    found(repo.get_organization(tenant_id))


def customer_request(tenant_id: UUID, profile: Record) -> CustomerRequest:
    return CustomerRequest(
        name=profile["name"], cpf_cnpj=profile["document"], email=profile["billing_email"],
        phone=profile["billing_phone"], external_reference=CUSTOMER_REFERENCE_PREFIX + str(tenant_id),
    )


def complete_profile(profile: Record | None) -> bool:
    return bool(
        profile and profile["document"] and profile["billing_email"] and profile["billing_phone"]
    )


def sync_customer(
    billing: BillingRepository, tenant_id: UUID, provider: AsaasClient | None, mode: str,
    *, as_member: bool = False,
) -> BillingCustomerStatus:
    """Cria ou localiza o cliente Asaas da empresa e grava o vínculo no ambiente atual.

    Regra: empresa ativa, com CPF/CNPJ, e-mail e telefone, e pelo menos um usuário com acesso.
    A referência externa da empresa torna a operação repetível sem duplicar clientes.
    """
    account = billing.account(tenant_id)
    if account and account["environment"] == mode:
        return "linked"
    if account and as_member:
        # Troca de ambiente exige revisão administrativa do vínculo existente.
        return "failed"
    profile = billing.customer_profile(tenant_id)
    if profile is None or not profile["active"]:
        return "inactive"
    if not complete_profile(profile):
        return "incomplete"
    if not as_member and not billing.has_active_member(tenant_id):
        return "no_access"
    if provider is None:
        return "unavailable"
    if not billing.try_lock_customer(tenant_id):
        return "busy"
    account = billing.account(tenant_id)
    if account and account["environment"] == mode:
        return "linked"
    request = customer_request(tenant_id, profile)
    try:
        customer = provider.find_customer(request.external_reference)
        if customer is None:
            customer = provider.create_customer(request)
        if not customer.notification_disabled:
            customer = provider.disable_notifications(customer.id)
    except AsaasRejected:
        return "rejected"
    except AsaasError:
        return "failed"
    if as_member:
        billing.link_customer_as_member(tenant_id, customer.id, mode)
    else:
        billing.save_account(tenant_id, customer.id, mode)
    return "linked"


def push_customer(
    billing: BillingRepository, tenant_id: UUID, provider: AsaasClient | None, mode: str,
) -> BillingCustomerStatus:
    """Atualiza no Asaas o cliente já vinculado ou cria o vínculo quando a empresa é elegível."""
    account = billing.account(tenant_id)
    if not account or account["environment"] != mode:
        return sync_customer(billing, tenant_id, provider, mode)
    profile = billing.customer_profile(tenant_id)
    if not complete_profile(profile):
        return "incomplete"
    if provider is None:
        return "linked"
    try:
        provider.update_customer(account["customer_id"], customer_request(tenant_id, profile))
    except AsaasRejected:
        return "rejected"
    except AsaasError:
        return "failed"
    return "linked"


def reference(invoice_id: UUID) -> str:
    return REFERENCE_PREFIX + str(invoice_id)


def apply_charge(repo: BillingRepository, invoice: Record, charge: AsaasCharge) -> Record:
    expected_id = invoice["provider_id"]
    amount = charge.amount_cents
    if charge.original_amount_cents is not None and charge.status in {
        "CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH", "REFUNDED",
    }:
        amount = charge.original_amount_cents
    if (
        (expected_id and charge.id != expected_id)
        or charge.customer != invoice["customer_id"]
        or charge.external_reference != reference(invoice["id"])
        or amount != invoice["total_cents"]
        or charge.due_date != invoice["due_date"]
    ):
        return repo.update_payment(
            invoice, state="linked" if expected_id else "uncertain", status="review",
            provider_id=expected_id,
        )
    status = "cancelled" if charge.deleted else STATUS.get(charge.status, "review")
    return repo.update_payment(
        invoice, state="linked", status=status, provider_id=charge.id,
        url=charge.invoice_url if status in {"open", "overdue", "processing"} else None,
    )


def sync_charge(repo: BillingRepository, invoice: Record, provider: AsaasClient) -> Record:
    if invoice["provider_state"] in {"unsubmitted", "rejected"}:
        return invoice
    try:
        if invoice["provider_id"]:
            charge = provider.get_charge(invoice["provider_id"])
        else:
            charge = provider.find_charge(invoice["customer_id"], reference(invoice["id"]))
    except AsaasError as exc:
        raise HTTPException(503, "Não foi possível consultar a cobrança no Asaas.") from exc
    return apply_charge(repo, invoice, charge) if charge else invoice


@router.get("/admin/clients/{tenant_id}/billing", response_model=AdminBilling)
def admin_billing(tenant_id: UUID, identity: CurrentUser, database: Data, request: Request, provider: Asaas):
    mode = environment(request)
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        status = sync_customer(billing, tenant_id, provider, mode)
        account = billing.account(tenant_id)
        return AdminBilling(
            environment=mode, account=Account(**account) if account else None,
            customer_status=status, customer_message=CUSTOMER_MESSAGES[status],
            customer_ready=status == "linked",
            payment_available=provider is not None,
            webhook_configured=bool(provider and provider.config.webhook_token),
            services=[Service(**row) for row in billing.services(tenant_id)],
            invoices=[invoice_view(row, mode, admin=True) for row in billing.invoices(tenant_id)],
        )


@router.put("/admin/clients/{tenant_id}/billing/account", response_model=Account)
def save_account(
    tenant_id: UUID, payload: AccountInput, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        if provider is not None:
            # Vínculo manual de cliente existente: confirma o ID e desliga as notificações pagas.
            try:
                customer = provider.get_customer(payload.customer_id)
                if customer.deleted:
                    raise AsaasRejected("Cliente removido no Asaas.")
                if not customer.notification_disabled:
                    provider.disable_notifications(customer.id)
            except AsaasRejected as exc:
                raise HTTPException(422, "Cliente não encontrado neste ambiente do Asaas.") from exc
            except AsaasError as exc:
                raise HTTPException(503, "Não foi possível confirmar o cliente no Asaas.") from exc
        return billing.save_account(tenant_id, payload.customer_id, environment(request))


@router.post("/admin/clients/{tenant_id}/billing/account/sync", response_model=AdminBilling)
def sync_account(
    tenant_id: UUID, identity: CurrentUser, database: Data, request: Request, provider: Asaas,
):
    return admin_billing(tenant_id, identity, database, request, provider)


@router.post("/admin/clients/{tenant_id}/billing/services", response_model=Service, status_code=201)
def create_service(tenant_id: UUID, payload: ServiceInput, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        return billing.create_service(tenant_id, **payload.model_dump())


@router.patch("/admin/clients/{tenant_id}/billing/services/{service_id}", response_model=Service)
def update_service(
    tenant_id: UUID, service_id: UUID, payload: ServiceInput, identity: CurrentUser, database: Data,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        return found(billing.update_service(tenant_id, service_id, **payload.model_dump()))


@router.post("/admin/clients/{tenant_id}/billing/invoices", response_model=Invoice, status_code=201)
def issue_invoice(
    tenant_id: UUID, payload: IssueInvoice, identity: CurrentUser, database: Data, request: Request,
):
    mode = environment(request)
    if payload.due_date < today():
        raise HTTPException(422, "Escolha um vencimento a partir de hoje.")
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        sync_customer(billing, tenant_id, request.app.state.asaas, mode)
        invoice = billing.issue_invoice(
            tenant_id, date.fromisoformat(payload.period + "-01"), payload.due_date, mode,
        )
        if invoice is None:
            raise HTTPException(409, "Vincule o cliente Asaas e mantenha entre 1 e 100 serviços ativos.")
        return invoice_view(invoice, mode, admin=True)


@router.post("/admin/clients/{tenant_id}/billing/invoices/{invoice_id}/cancel", response_model=Invoice)
def cancel_invoice(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data, request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        invoice = found(billing.locked_invoice(invoice_id, tenant_id))
        if invoice["status"] != "cancelled":
            if not can_cancel_invoice(invoice):
                raise HTTPException(409, "Esta fatura já foi encaminhada ao Asaas.")
            invoice = billing.update_payment(invoice, state=invoice["provider_state"], status="cancelled")
        return invoice_view(invoice, environment(request), admin=True)


@router.post("/admin/clients/{tenant_id}/billing/invoices/{invoice_id}/reconcile", response_model=Invoice)
def reconcile_invoice(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        invoice = found(billing.locked_invoice(invoice_id, tenant_id))
        if provider is None or invoice["environment"] != mode:
            raise HTTPException(503, "Integração Asaas indisponível para esta fatura.")
        return invoice_view(sync_charge(billing, invoice, provider), mode, admin=True)


@router.get("/tenants/{tenant_id}/billing", response_model=PortalBilling)
def portal_billing(tenant_id: UUID, identity: CurrentUser, database: Data, request: Request, provider: Asaas):
    mode = environment(request)
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        # O acesso do próprio cliente ao portal atende a regra de "usuário com acesso".
        status = sync_customer(billing, tenant_id, provider, mode, as_member=True)
        return PortalBilling(
            payment_available=provider is not None,
            customer_ready=status == "linked",
            invoices=[invoice_view(row, mode) for row in billing.invoices(tenant_id)],
        )


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/payment", response_model=Invoice)
def start_payment(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        billing.enable_portal_payment()
        invoice = found(billing.locked_invoice(invoice_id, tenant_id))
        if invoice["status"] in {"paid", "confirmed", "refunded", "cancelled", "review"}:
            return invoice_view(invoice, mode)
        if invoice["payment_url"] and invoice["environment"] == mode:
            return invoice_view(invoice, mode)
        if provider is None or invoice["environment"] != mode:
            raise HTTPException(503, "Integração Asaas indisponível para esta fatura.")
        if invoice["provider_state"] != "unsubmitted":
            return invoice_view(sync_charge(billing, invoice, provider), mode)
        if invoice["due_date"] < today():
            raise HTTPException(409, "Solicite ao administrador um novo vencimento para esta fatura.")
        invoice = billing.update_payment(invoice, state="inflight", status="processing")

    description = ", ".join(item["name"] for item in invoice["items"])
    charge = None
    try:
        charge = provider.create_charge(ChargeRequest(
            customer=invoice["customer_id"], billing_type="UNDEFINED",
            amount_cents=invoice["total_cents"], due_date=invoice["due_date"],
            description=f"FuturaNext {invoice['period']:%Y-%m} - {description}"[:500],
            external_reference=reference(invoice_id),
        ))
        state, status = "linked", "open"
    except AsaasRejected:
        state, status = "rejected", "review"
    except AsaasError as exc:
        if exc.status in {401, 403, 429}:
            state, status = "rejected", "review"
        else:
            state, status = "uncertain", "processing"

    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        billing.enable_portal_payment()
        current = found(billing.locked_invoice(invoice_id, tenant_id))
        if current["provider_id"]:
            if charge and charge.id != current["provider_id"]:
                current = apply_charge(billing, current, charge)
        elif charge:
            current = apply_charge(billing, current, charge)
        elif current["provider_state"] == "inflight" and current["status"] == "processing":
            current = billing.update_payment(current, state=state, status=status)
        return invoice_view(current, mode)


@router.post("/integrations/asaas/events", include_in_schema=False)
def asaas_webhook(
    event: PaymentEvent, database: Data, request: Request, provider: Asaas,
    token: Annotated[str | None, Header(alias="asaas-access-token")] = None,
):
    if provider is None or not provider.config.valid_webhook_token(token):
        raise HTTPException(401, "Webhook não autorizado.")
    if event.payment is None or not event.event.startswith("PAYMENT_"):
        return {"received": True}
    external = event.payment.externalReference or ""
    if not external.startswith(REFERENCE_PREFIX):
        return {"received": True}
    try:
        invoice_id = UUID(external[len(REFERENCE_PREFIX):])
    except ValueError:
        return {"received": True}
    mode = environment(request)
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        billing = BillingRepository(connection)
        billing.enable_webhook(mode)
        invoice = billing.locked_invoice(invoice_id, None)
        if not invoice or invoice["provider_state"] in {"unsubmitted", "rejected"}:
            return {"received": True}
        if invoice["provider_id"] and invoice["provider_id"] != event.payment.id:
            return {"received": True}
        if billing.record_event(mode, event.id, invoice_id):
            try:
                charge = provider.get_charge(event.payment.id)
            except AsaasError as exc:
                if (
                    exc.status == 404 and event.event == "PAYMENT_DELETED"
                    and invoice["provider_id"] == event.payment.id
                ):
                    billing.update_payment(
                        invoice, state="linked", status="cancelled", provider_id=event.payment.id,
                    )
                else:
                    raise HTTPException(503, "Não foi possível conciliar o evento do Asaas.") from exc
            else:
                apply_charge(billing, invoice, charge)
    return {"received": True}
