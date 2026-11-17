"""Criação e conciliação de cobranças na fatura hospedada pelo Asaas."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, DecimalException
from hmac import compare_digest
from typing import Literal
from urllib.parse import urlsplit

import httpx

AsaasEnvironment = Literal["sandbox", "production"]
BillingType = Literal["UNDEFINED", "BOLETO", "PIX", "CREDIT_CARD"]

API_BASE_URLS = {
    "sandbox": "https://api-sandbox.asaas.com/v3",
    "production": "https://api.asaas.com/v3",
}
INVOICE_HOSTS = {
    "sandbox": {"sandbox.asaas.com"},
    "production": {"asaas.com", "www.asaas.com"},
}
BILLING_TYPES = {"UNDEFINED", "BOLETO", "PIX", "CREDIT_CARD"}
WEBHOOK_TOKEN_HEADER = "asaas-access-token"
CUSTOMER_ID = re.compile(r"cus_[A-Za-z0-9_-]{1,120}")
PAYMENT_ID = re.compile(r"pay_[A-Za-z0-9_-]{1,120}")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
INVOICE_PATH = re.compile(r"/i/[A-Za-z0-9_-]{1,160}/?")
PAYMENT_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,59}")


class AsaasError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AsaasRejected(AsaasError):
    """Dados inválidos ou rejeição da operação confirmada pelo provedor."""


class AsaasUnavailable(AsaasError):
    """A configuração ou o provedor não está disponível."""


class AsaasUncertainCreation(AsaasError):
    """O POST pode ter criado uma cobrança. Concilie a referência antes de outra ação."""


class AsaasReconciliationError(AsaasError):
    """A resposta não identifica uma cobrança inequívoca."""


@dataclass(frozen=True)
class AsaasConfig:
    api_key: str = field(repr=False)
    environment: AsaasEnvironment = "sandbox"
    webhook_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.environment not in API_BASE_URLS:
            raise ValueError("FN_ASAAS_ENVIRONMENT precisa ser sandbox ou production.")
        if not self.api_key or any(not 32 < ord(c) < 127 for c in self.api_key):
            raise ValueError("FN_ASAAS_API_KEY precisa conter uma chave Asaas válida.")
        if self.webhook_token and compare_digest(
            self.api_key.encode(), self.webhook_token.encode()
        ):
            raise ValueError("Use um token de webhook diferente da chave de API do Asaas.")

    def valid_webhook_token(self, received: str | None) -> bool:
        if not self.webhook_token or not received:
            return False
        return compare_digest(self.webhook_token.encode(), received.encode())


@dataclass(frozen=True)
class ChargeRequest:
    customer: str
    billing_type: BillingType
    amount_cents: int
    due_date: date
    description: str
    external_reference: str


@dataclass(frozen=True)
class AsaasCharge:
    id: str
    customer: str
    billing_type: BillingType
    amount_cents: int
    due_date: date
    external_reference: str
    status: str
    invoice_url: str | None = field(repr=False)
    deleted: bool = False
    original_amount_cents: int | None = None


def _amount_cents(value: object) -> int:
    if type(value) not in {int, Decimal}:
        raise ValueError
    amount = Decimal(value)
    if not amount.is_finite() or amount <= 0 or amount.quantize(Decimal("0.01")) != amount:
        raise ValueError
    return int(amount.scaleb(2))


def hosted_invoice_url(value: object, environment: AsaasEnvironment) -> str | None:
    if not isinstance(value, str) or not value or any(not 32 < ord(char) < 127 for char in value):
        return None
    try:
        url = urlsplit(value)
        hostname = url.hostname
        if (
            url.scheme != "https"
            or hostname not in INVOICE_HOSTS.get(environment, ())
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or url.query
            or url.fragment
            or not INVOICE_PATH.fullmatch(url.path)
        ):
            return None
    except ValueError:
        return None
    return f"https://{hostname}{url.path}"


def _check_reference(customer: str, reference: str) -> None:
    if (
        not isinstance(customer, str)
        or not CUSTOMER_ID.fullmatch(customer)
        or not isinstance(reference, str)
        or not REFERENCE.fullmatch(reference)
    ):
        raise AsaasRejected("Cliente Asaas ou referência da cobrança inválida.")


def _request_value(request: ChargeRequest) -> float:
    if request.billing_type not in BILLING_TYPES or type(request.due_date) is not date:
        raise AsaasRejected("Forma de pagamento ou vencimento inválido.")
    if not 1 <= len(request.description.strip()) <= 500:
        raise AsaasRejected("A descrição deve ter de 1 a 500 caracteres.")
    if type(request.amount_cents) is not int or request.amount_cents <= 0:
        raise AsaasRejected("Informe um valor positivo em centavos de real.")
    try:
        value = request.amount_cents / 100
        if Decimal(str(value)) * 100 != request.amount_cents:
            raise ValueError
    except (ValueError, OverflowError, DecimalException):
        raise AsaasRejected("O valor ultrapassa a precisão disponível.") from None
    return value


class AsaasClient:
    def __init__(self, client: httpx.Client, config: AsaasConfig):
        self.client = client
        self.config = config

    def create_charge(self, request: ChargeRequest) -> AsaasCharge:
        _check_reference(request.customer, request.external_reference)
        payload = {
            "customer": request.customer,
            "billingType": request.billing_type,
            "value": _request_value(request),
            "dueDate": request.due_date.isoformat(),
            "description": request.description.strip(),
            "externalReference": request.external_reference,
        }
        data = self._request("POST", "/payments", json=payload)
        try:
            charge = self._charge(data)
            if (
                charge.customer != request.customer
                or charge.external_reference != request.external_reference
                or charge.amount_cents != request.amount_cents
                or charge.due_date != request.due_date
                or (
                    request.billing_type != "UNDEFINED"
                    and charge.billing_type != request.billing_type
                )
                or charge.deleted
            ):
                raise AsaasReconciliationError(
                    "Os dados da cobrança retornada divergem da solicitação."
                )
        except AsaasReconciliationError:
            raise AsaasUncertainCreation(
                "Não foi possível confirmar a cobrança criada. Concilie a referência no Asaas."
            ) from None
        return charge

    def find_charge(self, customer: str, external_reference: str) -> AsaasCharge | None:
        _check_reference(customer, external_reference)
        page = self._request(
            "GET",
            "/payments",
            params={
                "customer": customer,
                "externalReference": external_reference,
                "offset": 0,
                "limit": 2,
            },
        )
        results = page.get("data")
        has_more = page.get("hasMore")
        if not isinstance(results, list) or type(has_more) is not bool:
            raise AsaasReconciliationError("Listagem do Asaas inválida para conciliação.")
        if has_more or len(results) > 1:
            raise AsaasReconciliationError("Há mais de uma cobrança para a mesma referência.")
        if not results:
            return None
        charge = self._charge(results[0])
        if charge.customer != customer or charge.external_reference != external_reference:
            raise AsaasReconciliationError(
                "Os dados retornados não correspondem à referência solicitada."
            )
        return charge

    def get_charge(self, payment_id: str) -> AsaasCharge:
        if not PAYMENT_ID.fullmatch(payment_id):
            raise AsaasRejected("Identificador de cobrança Asaas inválido.")
        charge = self._charge(self._request("GET", f"/payments/{payment_id}"))
        if charge.id != payment_id:
            raise AsaasReconciliationError(
                "O identificador retornado diverge da cobrança solicitada."
            )
        return charge

    def _request(self, method: str, path: str, **options) -> dict:
        creating = method == "POST"
        try:
            response = self.client.request(
                method,
                f"{API_BASE_URLS[self.config.environment]}{path}",
                headers={
                    "accept": "application/json",
                    "user-agent": "FuturaNext/1.0",
                    "access_token": self.config.api_key,
                },
                follow_redirects=False,
                timeout=8,
                **options,
            )
        except httpx.RequestError:
            if creating:
                raise AsaasUncertainCreation(
                    "A criação da cobrança não foi confirmada. Concilie a referência no Asaas."
                ) from None
            raise AsaasUnavailable("Não foi possível consultar o Asaas.") from None
        if not response.is_success:
            status = response.status_code
            if status in {400, 404, 422}:
                raise AsaasRejected("O Asaas rejeitou os dados da cobrança.", status)
            if creating and status not in {401, 403, 429}:
                raise AsaasUncertainCreation(
                    "O resultado da criação da cobrança é incerto. Concilie a referência no Asaas.",
                    status,
                )
            raise AsaasUnavailable("O Asaas está indisponível para esta operação.", status)
        try:
            data = response.json(parse_float=Decimal)
            if isinstance(data, dict):
                return data
        except (ValueError, DecimalException):
            pass
        if creating:
            raise AsaasUncertainCreation(
                "Resposta de criação inválida. Concilie a referência no Asaas."
            )
        raise AsaasReconciliationError("O Asaas retornou uma resposta inválida.")

    def _charge(self, data: object) -> AsaasCharge:
        try:
            if not isinstance(data, dict):
                raise TypeError
            payment_id, customer = data["id"], data["customer"]
            method, status = data["billingType"], data["status"]
            reference, due_date = data["externalReference"], data["dueDate"]
            amount = _amount_cents(data["value"])
            original = data.get("originalValue")
            deleted = data.get("deleted", False)
            invoice = hosted_invoice_url(data.get("invoiceUrl"), self.config.environment)
            if (
                not PAYMENT_ID.fullmatch(payment_id)
                or not CUSTOMER_ID.fullmatch(customer)
                or method not in BILLING_TYPES
                or not PAYMENT_STATUS.fullmatch(status)
                or not REFERENCE.fullmatch(reference)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date)
                or type(deleted) is not bool
                or (not deleted and invoice is None)
            ):
                raise ValueError
            return AsaasCharge(
                payment_id,
                customer,
                method,
                amount,
                date.fromisoformat(due_date),
                reference,
                status,
                invoice,
                deleted,
                _amount_cents(original) if original is not None else None,
            )
        except (TypeError, KeyError, ValueError, OverflowError, DecimalException):
            raise AsaasReconciliationError(
                "Os dados da cobrança retornada pelo Asaas são inválidos."
            ) from None
