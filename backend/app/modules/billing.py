from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import text

from ..dependencies import CurrentUser, Data, require_permission, tenant_repository
from ..repository import Record
from ..schemas import BillingCustomerStatus, BillingEmail, Document, Input, Name, Phone
from .asaas import (
    AsaasCardRefused,
    AsaasCharge,
    AsaasClient,
    AsaasError,
    AsaasRejected,
    AsaasUncertainCreation,
    CardHolder,
    ChargeRequest,
    CreditCard,
    CustomerRequest,
    SubscriptionRequest,
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
SUBSCRIPTION_REFERENCE_PREFIX = "futuranext:subscription:"
SUBSCRIPTION_DESCRIPTION = "Mensalidade FuturaNext"
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


class BoletoView(BaseModel):
    identification_field: str
    bar_code: str
    pdf_url: str | None


class PixView(BaseModel):
    image: str
    payload: str
    expiration: str | None


def luhn(number: str) -> bool:
    total = 0
    for index, digit in enumerate(int(char) for char in reversed(number)):
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


class CardInput(BaseModel):
    """Dados do cartão: só trafegam até o Asaas. Não são gravados nem registrados em log."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True)

    holder_name: Annotated[str, StringConstraints(min_length=2, max_length=100)]
    number: SecretStr
    expiry_month: Annotated[int, Field(ge=1, le=12)]
    expiry_year: Annotated[int, Field(ge=2000, le=2100)]
    ccv: SecretStr
    cpf_cnpj: Document
    email: BillingEmail
    phone: Phone
    postal_code: Annotated[str, StringConstraints(pattern=r"^[0-9]{5}-?[0-9]{3}$")]
    address_number: Annotated[str, StringConstraints(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def validate_card(self) -> "CardInput":
        number = "".join(self.number.get_secret_value().split())
        if not (number.isdigit() and 13 <= len(number) <= 19 and luhn(number)):
            raise ValueError("Número de cartão inválido.")
        ccv = self.ccv.get_secret_value()
        if not (ccv.isdigit() and len(ccv) in {3, 4}):
            raise ValueError("Código de segurança inválido.")
        current = today()
        if (self.expiry_year, self.expiry_month) < (current.year, current.month):
            raise ValueError("Cartão vencido.")
        self.number = SecretStr(number)
        return self


class CardResult(BaseModel):
    invoice: Invoice
    message: str
    challenge_url: str | None = None


class SubscriptionView(BaseModel):
    enabled: bool
    status: Literal["active", "paused", "pending", "error"]
    value_cents: int | None
    next_due_date: date | None


class SubscriptionInput(Input):
    enabled: bool
    next_due_date: date | None = None


class PlanView(BaseModel):
    trial_ends_at: AwareDatetime | None
    trial_active: bool
    subscription: SubscriptionView | None


class PurchaseResult(BaseModel):
    subscription: SubscriptionView | None


class PortalBilling(BaseModel):
    payment_available: bool
    customer_ready: bool = False
    invoices: list[Invoice]
    subscription: SubscriptionView | None = None


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
    subscription: Annotated[str, StringConstraints(pattern=r"^sub_[a-zA-Z0-9_-]{1,120}$")] | None = None


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
    if invoice.get("subscription_id"):
        # Fatura da assinatura: a identidade é a cobrança, o cliente e a assinatura de origem.
        if (
            charge.id != expected_id or charge.customer != invoice["customer_id"]
            or charge.subscription != invoice["subscription_id"]
        ):
            return repo.update_payment(invoice, state="linked", status="review", provider_id=expected_id)
        status = "cancelled" if charge.deleted else STATUS.get(charge.status, "review")
        return repo.update_payment(
            invoice, state="linked", status=status, provider_id=charge.id,
            url=charge.invoice_url if status in {"open", "overdue", "processing"} else None,
        )
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


def subscription_reference(tenant_id: UUID) -> str:
    return SUBSCRIPTION_REFERENCE_PREFIX + str(tenant_id)


def subscription_view(row: Record | None) -> SubscriptionView | None:
    if row is None:
        return None
    if not row["enabled"]:
        status = "paused"
    elif row["provider_state"] == "linked":
        status = "active"
    elif row["provider_state"] in {"rejected", "uncertain"}:
        status = "error"
    else:
        status = "pending"
    return SubscriptionView(
        enabled=row["enabled"], status=status, value_cents=row["value_cents"],
        next_due_date=row["next_due_date"],
    )


def push_subscription(
    billing: BillingRepository, tenant_id: UUID, provider: AsaasClient | None, mode: str,
    *, reactivate: bool = False,
) -> Record | None:
    """Leva ao Asaas a situação gravada: liga/desliga e valor = soma dos serviços ativos.

    Sem serviço ativo a assinatura fica pausada. A criação é conciliada pela referência da empresa,
    então repetir a chamada não duplica a assinatura.
    """
    row = billing.subscription(tenant_id)
    if row is None or provider is None or row["environment"] != mode:
        return row
    account = billing.account(tenant_id)
    if not account or account["environment"] != mode or not billing.try_lock_subscription(tenant_id):
        return row
    total = billing.active_total(tenant_id)
    try:
        if not row["provider_id"]:
            if not row["enabled"] or not total:
                return billing.save_subscription(tenant_id, mode, value_cents=total or None)
            request = SubscriptionRequest(
                customer=account["customer_id"], amount_cents=total,
                next_due_date=max(row["next_due_date"] or today(), today()),
                description=SUBSCRIPTION_DESCRIPTION, external_reference=subscription_reference(tenant_id),
            )
            try:
                subscription = provider.find_subscription(request.customer, request.external_reference)
                if subscription is None:
                    subscription = provider.create_subscription(request)
            except AsaasUncertainCreation:
                return billing.save_subscription(
                    tenant_id, mode, provider_state="uncertain", value_cents=total,
                )
            if subscription.amount_cents != total:
                subscription = provider.update_subscription(subscription.id, amount_cents=total)
            return billing.save_subscription(
                tenant_id, mode, provider_id=subscription.id, provider_state="linked",
                value_cents=total, next_due_date=subscription.next_due_date,
            )
        active = bool(row["enabled"] and total)
        changes: dict = {}
        if active and total != row["value_cents"]:
            changes["amount_cents"] = total
        if reactivate or not active:
            changes["active"] = active
        if reactivate and active and row["next_due_date"]:
            changes["next_due_date"] = max(row["next_due_date"], today())
        if not changes:
            return row
        subscription = provider.update_subscription(row["provider_id"], **changes)
        return billing.save_subscription(
            tenant_id, mode, provider_state="linked", value_cents=total or row["value_cents"],
            next_due_date=subscription.next_due_date,
        )
    except AsaasRejected:
        return billing.save_subscription(tenant_id, mode, provider_state="rejected")
    except AsaasError:
        if row["provider_id"]:
            return row
        return billing.save_subscription(tenant_id, mode, provider_state="uncertain")


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
            subscription=subscription_view(billing.subscription(tenant_id)),
        )


@router.put("/admin/clients/{tenant_id}/billing/subscription", response_model=AdminBilling)
def save_subscription(
    tenant_id: UUID, payload: SubscriptionInput, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    """Liga ou desliga a cobrança mensal automática da empresa."""
    mode = environment(request)
    if payload.enabled and payload.next_due_date and payload.next_due_date < today():
        raise HTTPException(422, "Escolha um vencimento a partir de hoje.")
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        current = billing.subscription(tenant_id)
        values: dict = {"enabled": payload.enabled}
        if current and current["environment"] != mode:
            values.update(provider_id=None, provider_state="unsubmitted")
        if payload.enabled:
            status = sync_customer(billing, tenant_id, provider, mode)
            if status != "linked":
                raise HTTPException(409, CUSTOMER_MESSAGES[status])
            if not billing.active_total(tenant_id):
                raise HTTPException(409, "Cadastre pelo menos um serviço ativo antes de ligar a cobrança.")
            due = payload.next_due_date or (current["next_due_date"] if current else None)
            if due is None:
                raise HTTPException(422, "Informe o primeiro vencimento da cobrança mensal.")
            values["next_due_date"] = due
        elif current is None:
            values["next_due_date"] = None
        billing.save_subscription(tenant_id, mode, **values)
        push_subscription(billing, tenant_id, provider, mode, reactivate=payload.enabled)
    return admin_billing(tenant_id, identity, database, request, provider)


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
def create_service(
    tenant_id: UUID, payload: ServiceInput, identity: CurrentUser, database: Data, request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        service = billing.create_service(tenant_id, **payload.model_dump())
        # Com a cobrança mensal ligada, o novo valor vale a partir da próxima fatura.
        push_subscription(billing, tenant_id, request.app.state.asaas, environment(request))
        return service


@router.patch("/admin/clients/{tenant_id}/billing/services/{service_id}", response_model=Service)
def update_service(
    tenant_id: UUID, service_id: UUID, payload: ServiceInput, identity: CurrentUser, database: Data,
    request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        billing = BillingRepository(repo.connection)
        billing_admin(billing, tenant_id)
        service = found(billing.update_service(tenant_id, service_id, **payload.model_dump()))
        push_subscription(billing, tenant_id, request.app.state.asaas, environment(request))
        return service


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
            subscription=subscription_view(billing.subscription(tenant_id)),
        )


@router.get("/tenants/{tenant_id}/plan", response_model=PlanView)
def portal_plan(tenant_id: UUID, identity: CurrentUser, database: Data):
    """Teste grátis e assinatura da empresa, sem chamar o Asaas."""
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        trial = billing.trial(tenant_id)
        now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        return PlanView(
            trial_ends_at=trial["ends_at"] if trial else None,
            trial_active=bool(trial and trial["ends_at"] > now),
            subscription=subscription_view(billing.subscription(tenant_id)),
        )


def first_due_date(trial: Record | None) -> date:
    """Primeira mensalidade: no fim do teste grátis ou, sem teste em andamento, em três dias."""
    if trial:
        ends = trial["ends_at"].astimezone(ZoneInfo("America/Sao_Paulo")).date()
        if ends > today():
            return ends
    return today() + timedelta(days=3)


@router.post("/tenants/{tenant_id}/offers/{product_id}/purchase", response_model=PurchaseResult)
def purchase_tool(
    tenant_id: UUID, product_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    """Compra imediata: libera a ferramenta e soma a mensalidade dela à assinatura da empresa."""
    mode = environment(request)
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        if not billing.purchase_tool(tenant_id, product_id):
            raise HTTPException(
                403, "Somente o administrador da empresa pode contratar, e a ferramenta precisa ter preço.",
            )
        sync_customer(billing, tenant_id, provider, mode, as_member=True)
        billing.enable_subscription_sync()
        current = billing.subscription(tenant_id)
        if current is None or current["environment"] != mode:
            billing.save_subscription(
                tenant_id, mode, enabled=True, provider_id=None, provider_state="unsubmitted",
                next_due_date=first_due_date(billing.trial(tenant_id)),
            )
        row = push_subscription(billing, tenant_id, provider, mode)
        return PurchaseResult(subscription=subscription_view(row))


def ensure_charge(
    database, identity, tenant_id: UUID, invoice_id: UUID, mode: str, provider: AsaasClient | None,
) -> Record:
    """Garante a cobrança da fatura no Asaas (forma UNDEFINED) e devolve o registro atualizado."""
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        billing.enable_portal_payment()
        invoice = found(billing.locked_invoice(invoice_id, tenant_id))
        if invoice["status"] in {"paid", "confirmed", "refunded", "cancelled", "review"}:
            return invoice
        if invoice["payment_url"] and invoice["environment"] == mode:
            return invoice
        if provider is None or invoice["environment"] != mode:
            raise HTTPException(503, "Integração Asaas indisponível para esta fatura.")
        if invoice["provider_state"] != "unsubmitted":
            return sync_charge(billing, invoice, provider)
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
        return current


def payable(invoice: Record, mode: str, provider: AsaasClient | None) -> str:
    if provider is None or invoice["environment"] != mode:
        raise HTTPException(503, "Integração Asaas indisponível para esta fatura.")
    if invoice["status"] in {"paid", "confirmed", "refunded"}:
        raise HTTPException(409, "Esta fatura já foi paga.")
    if invoice["status"] not in {"open", "overdue"} or not invoice["provider_id"]:
        raise HTTPException(
            409, "O pagamento desta fatura está em processamento. Consulte novamente em instantes.",
        )
    return invoice["provider_id"]


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/payment", response_model=Invoice)
def start_payment(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    return invoice_view(ensure_charge(database, identity, tenant_id, invoice_id, mode, provider), mode)


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/payment/boleto", response_model=BoletoView)
def boleto_payment(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    payment_id = payable(
        ensure_charge(database, identity, tenant_id, invoice_id, mode, provider), mode, provider,
    )
    try:
        slip = provider.boleto(payment_id)
        charge = provider.get_charge(payment_id)
    except AsaasError as exc:
        raise HTTPException(503, "O boleto está indisponível no momento. Tente novamente.") from exc
    return BoletoView(
        identification_field=slip.identification_field, bar_code=slip.bar_code,
        pdf_url=charge.bank_slip_url,
    )


@router.get("/tenants/{tenant_id}/billing/invoices/{invoice_id}/payment/boleto.pdf")
def boleto_pdf(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        billing.enable_portal_payment()
        invoice = found(billing.locked_invoice(invoice_id, tenant_id))
    payment_id = payable(invoice, mode, provider)
    try:
        url = provider.get_charge(payment_id).bank_slip_url
        if url is None:
            raise HTTPException(409, "O PDF do boleto ainda não está disponível.")
        content = provider.bank_slip_pdf(url)
    except AsaasError as exc:
        raise HTTPException(503, "O PDF do boleto está indisponível. Tente novamente.") from exc
    filename = f"boleto-futuranext-{invoice['period']:%Y-%m}.pdf"
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/payment/pix", response_model=PixView)
def pix_payment(
    tenant_id: UUID, invoice_id: UUID, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    payment_id = payable(
        ensure_charge(database, identity, tenant_id, invoice_id, mode, provider), mode, provider,
    )
    try:
        pix = provider.pix(payment_id)
    except AsaasError as exc:
        raise HTTPException(503, "O Pix está indisponível no momento. Tente novamente.") from exc
    return PixView(
        image=f"data:image/png;base64,{pix.encoded_image}", payload=pix.payload,
        expiration=pix.expiration,
    )


CARD_MESSAGES = {
    "confirmed": "Pagamento aprovado. Obrigado!",
    "paid": "Pagamento aprovado. Obrigado!",
    "processing": "Pagamento em análise pelo Asaas. Você será avisado quando for confirmado.",
}


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/payment/card", response_model=CardResult)
def card_payment(
    tenant_id: UUID, invoice_id: UUID, card: CardInput, identity: CurrentUser, database: Data,
    request: Request, provider: Asaas,
):
    mode = environment(request)
    payable(ensure_charge(database, identity, tenant_id, invoice_id, mode, provider), mode, provider)
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        billing = BillingRepository(repo.connection)
        billing.enable_portal_payment()
        # A trava da fatura impede dois pagamentos simultâneos da mesma cobrança.
        invoice = found(billing.locked_invoice(invoice_id, tenant_id))
        payment_id = payable(invoice, mode, provider)
        try:
            invoice = apply_charge(billing, invoice, provider.get_charge(payment_id))
        except AsaasError as exc:
            raise HTTPException(503, "Não foi possível consultar a cobrança no Asaas.") from exc
        payment_id = payable(invoice, mode, provider)
        try:
            charge = provider.pay_with_card(
                payment_id,
                CreditCard(
                    holder_name=card.holder_name, number=card.number.get_secret_value(),
                    expiry_month=f"{card.expiry_month:02d}", expiry_year=str(card.expiry_year),
                    ccv=card.ccv.get_secret_value(),
                ),
                CardHolder(
                    name=card.holder_name, email=card.email, cpf_cnpj=card.cpf_cnpj,
                    postal_code=card.postal_code.replace("-", ""),
                    address_number=card.address_number, phone=card.phone,
                ),
            )
        except AsaasCardRefused as exc:
            raise HTTPException(422, str(exc)) from None
        except AsaasUncertainCreation:
            invoice = billing.update_payment(
                invoice, state="linked", status="processing", provider_id=payment_id,
            )
            return CardResult(
                invoice=invoice_view(invoice, mode),
                message="Não recebemos a confirmação do Asaas. Não pague de novo: consulte em instantes.",
            )
        except AsaasError as exc:
            raise HTTPException(503, "O pagamento com cartão está indisponível agora.") from exc
        invoice = apply_charge(billing, invoice, charge)
        view = invoice_view(invoice, mode)
        if charge.challenge_url:
            return CardResult(
                invoice=view, challenge_url=charge.challenge_url,
                message="O banco emissor pediu uma confirmação adicional. Conclua na página do banco.",
            )
        return CardResult(
            invoice=view,
            message=CARD_MESSAGES.get(view.status, "Situação do pagamento atualizada."),
        )


@router.post("/integrations/asaas/events", include_in_schema=False)
def asaas_webhook(
    event: PaymentEvent, database: Data, request: Request, provider: Asaas,
    token: Annotated[str | None, Header(alias="asaas-access-token")] = None,
):
    if provider is None or not provider.config.valid_webhook_token(token):
        raise HTTPException(401, "Webhook não autorizado.")
    if event.payment is None or not event.event.startswith("PAYMENT_"):
        return {"received": True}
    if event.payment.subscription:
        return subscription_payment_event(event.payment, database, environment(request), provider)
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


PAID = {"CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH", "REFUNDED"}


def subscription_payment_event(
    payment: EventPayment, database, mode: str, provider: AsaasClient,
) -> dict[str, bool]:
    """Registra ou atualiza a fatura de uma cobrança mensal. Os dados vêm do Asaas, não do evento."""
    try:
        charge = provider.get_charge(payment.id)
    except AsaasError as exc:
        if exc.status == 404:
            return {"received": True}
        raise HTTPException(503, "Não foi possível conciliar o evento do Asaas.") from exc
    if charge.subscription != payment.subscription:
        return {"received": True}
    status = "cancelled" if charge.deleted else STATUS.get(charge.status, "review")
    amount = charge.amount_cents
    if charge.original_amount_cents is not None and charge.status in PAID:
        amount = charge.original_amount_cents
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        billing = BillingRepository(connection)
        billing.enable_webhook(mode)
        billing.record_subscription_payment(
            mode, charge.subscription, charge.id, charge.due_date, amount, status,
            charge.invoice_url if status in {"open", "overdue", "processing"} else None,
        )
    return {"received": True}
