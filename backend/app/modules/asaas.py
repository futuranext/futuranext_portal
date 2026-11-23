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
SUBSCRIPTION_ID = re.compile(r"sub_[A-Za-z0-9_-]{1,120}")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
INVOICE_PATH = re.compile(r"/i/[A-Za-z0-9_-]{1,160}/?")
BANK_SLIP_PATH = re.compile(r"/b/pdf/[A-Za-z0-9_-]{1,160}/?")
MAX_PDF_BYTES = 3 * 1024 * 1024
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
    external_reference: str | None
    status: str
    invoice_url: str | None = field(repr=False)
    deleted: bool = False
    original_amount_cents: int | None = None
    bank_slip_url: str | None = field(default=None, repr=False)
    challenge_url: str | None = field(default=None, repr=False)
    subscription: str | None = None


@dataclass(frozen=True)
class SubscriptionRequest:
    """Assinatura mensal da empresa. A forma de pagamento fica em aberto (boleto, Pix ou cartão)."""

    customer: str
    amount_cents: int
    next_due_date: date
    description: str
    external_reference: str


@dataclass(frozen=True)
class AsaasSubscription:
    id: str
    customer: str
    amount_cents: int
    next_due_date: date
    status: str
    external_reference: str | None
    deleted: bool = False


@dataclass(frozen=True)
class AsaasBoleto:
    identification_field: str
    bar_code: str


@dataclass(frozen=True)
class AsaasPix:
    encoded_image: str = field(repr=False)
    payload: str = field(repr=False)
    expiration: str | None = None


@dataclass(frozen=True)
class CreditCard:
    """Dados do cartão usados só na chamada ao Asaas. Nunca registrar nem persistir."""

    holder_name: str
    number: str = field(repr=False)
    expiry_month: str = field(repr=False)
    expiry_year: str = field(repr=False)
    ccv: str = field(repr=False)


@dataclass(frozen=True)
class CardHolder:
    name: str
    email: str
    cpf_cnpj: str
    postal_code: str
    address_number: str
    phone: str


class AsaasCardRefused(AsaasError):
    """O Asaas recusou o pagamento com cartão. A mensagem pode ser mostrada ao pagador."""


@dataclass(frozen=True)
class CustomerRequest:
    """Cliente Asaas da empresa. Notificações pagas do Asaas ficam sempre desativadas."""

    name: str
    cpf_cnpj: str
    email: str
    phone: str
    external_reference: str


@dataclass(frozen=True)
class AsaasCustomer:
    id: str
    external_reference: str | None
    notification_disabled: bool
    deleted: bool = False


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


def bank_slip_url(value: object, environment: AsaasEnvironment) -> str | None:
    if not isinstance(value, str) or not value or any(not 32 < ord(char) < 127 for char in value):
        return None
    try:
        url = urlsplit(value)
        hostname = url.hostname
        if (
            url.scheme != "https" or hostname not in INVOICE_HOSTS.get(environment, ())
            or url.username is not None or url.password is not None
            or url.port not in (None, 443) or url.query or url.fragment
            or not BANK_SLIP_PATH.fullmatch(url.path)
        ):
            return None
    except ValueError:
        return None
    return f"https://{hostname}{url.path}"


def _challenge_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme != "https" or not url.hostname or url.username is not None:
        return None
    return value


def _refusal(response: httpx.Response) -> str:
    try:
        description = response.json()["errors"][0]["description"]
    except (ValueError, AttributeError, TypeError, KeyError, IndexError):
        description = None
    if isinstance(description, str) and 0 < len(description) <= 300:
        return description
    return "Pagamento não autorizado. Confira os dados do cartão e tente novamente."


def _customer_payload(request: CustomerRequest) -> dict:
    phone = request.phone
    payload = {
        "name": request.name.strip()[:120],
        "cpfCnpj": request.cpf_cnpj,
        "email": request.email,
        "externalReference": request.external_reference,
        # Não marcar "Enviar notificação de cobrança por e-mail e SMS": o recurso tem custo extra.
        "notificationDisabled": True,
    }
    # Celular tem 11 dígitos com o nono dígito; os demais seguem como telefone fixo.
    payload["mobilePhone" if len(phone) == 11 else "phone"] = phone
    return payload


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

    def create_customer(self, request: CustomerRequest) -> AsaasCustomer:
        """Cria o cliente com notificationDisabled=true (notificações por e-mail/SMS são pagas)."""
        if not REFERENCE.fullmatch(request.external_reference):
            raise AsaasRejected("Referência do cliente inválida.")
        customer = self._customer(
            self._request("POST", "/customers", json=_customer_payload(request))
        )
        if customer.external_reference != request.external_reference or customer.deleted:
            raise AsaasUncertainCreation(
                "Não foi possível confirmar o cliente criado. Concilie a referência no Asaas."
            )
        return customer

    def update_customer(self, customer_id: str, request: CustomerRequest) -> AsaasCustomer:
        if not CUSTOMER_ID.fullmatch(customer_id):
            raise AsaasRejected("Identificador de cliente Asaas inválido.")
        payload = _customer_payload(request)
        payload.pop("externalReference")
        customer = self._customer(self._request("POST", f"/customers/{customer_id}", json=payload))
        if customer.id != customer_id:
            raise AsaasReconciliationError("O identificador retornado diverge do cliente.")
        return customer

    def find_customer(self, external_reference: str) -> AsaasCustomer | None:
        if not REFERENCE.fullmatch(external_reference):
            raise AsaasRejected("Referência do cliente inválida.")
        page = self._request(
            "GET", "/customers",
            params={"externalReference": external_reference, "offset": 0, "limit": 2},
        )
        results = page.get("data")
        if not isinstance(results, list) or type(page.get("hasMore")) is not bool:
            raise AsaasReconciliationError("Listagem de clientes do Asaas inválida.")
        customers = [
            item for item in (self._customer(result) for result in results) if not item.deleted
        ]
        if page["hasMore"] or len(customers) > 1:
            raise AsaasReconciliationError("Há mais de um cliente Asaas para a mesma empresa.")
        if not customers:
            return None
        if customers[0].external_reference != external_reference:
            raise AsaasReconciliationError("O cliente retornado não corresponde à empresa.")
        return customers[0]

    def boleto(self, payment_id: str) -> AsaasBoleto:
        if not PAYMENT_ID.fullmatch(payment_id):
            raise AsaasRejected("Identificador de cobrança Asaas inválido.")
        data = self._request("GET", f"/payments/{payment_id}/identificationField")
        line, bar = data.get("identificationField"), data.get("barCode")
        if not (isinstance(line, str) and re.fullmatch(r"[0-9 .]{40,60}", line)
                and isinstance(bar, str) and re.fullmatch(r"[0-9]{44}", bar)):
            raise AsaasReconciliationError("O Asaas não retornou a linha digitável do boleto.")
        return AsaasBoleto(re.sub(r"[^0-9]", "", line), bar)

    def pix(self, payment_id: str) -> AsaasPix:
        if not PAYMENT_ID.fullmatch(payment_id):
            raise AsaasRejected("Identificador de cobrança Asaas inválido.")
        data = self._request("GET", f"/payments/{payment_id}/pixQrCode")
        image, payload = data.get("encodedImage"), data.get("payload")
        expiration = data.get("expirationDate")
        if not (isinstance(image, str) and re.fullmatch(r"[A-Za-z0-9+/=]{100,400000}", image)
                and isinstance(payload, str) and 20 <= len(payload) <= 1000
                and all(32 <= ord(char) < 127 for char in payload)):
            raise AsaasReconciliationError("O Asaas não retornou o QR Code Pix.")
        return AsaasPix(image, payload, expiration if isinstance(expiration, str) else None)

    def bank_slip_pdf(self, url: str) -> bytes:
        if bank_slip_url(url, self.config.environment) != url:
            raise AsaasRejected("Endereço do boleto inválido.")
        try:
            with self.client.stream(
                "GET", url, headers={"user-agent": "FuturaNext/1.0"},
                follow_redirects=True, timeout=15,
            ) as response:
                if not response.is_success:
                    raise AsaasUnavailable("O PDF do boleto está indisponível.", response.status_code)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content += chunk
                    if len(content) > MAX_PDF_BYTES:
                        raise AsaasReconciliationError("O PDF do boleto excede o tamanho esperado.")
        except httpx.RequestError:
            raise AsaasUnavailable("O PDF do boleto está indisponível.") from None
        if not content.startswith(b"%PDF-"):
            raise AsaasReconciliationError("O Asaas não retornou um PDF.")
        return bytes(content)

    def pay_with_card(self, payment_id: str, card: CreditCard, holder: CardHolder) -> AsaasCharge:
        """Paga a cobrança na hora. Não é idempotente: em resultado incerto, consulte antes."""
        if not PAYMENT_ID.fullmatch(payment_id):
            raise AsaasRejected("Identificador de cobrança Asaas inválido.")
        body = {
            "creditCard": {
                "holderName": card.holder_name, "number": card.number,
                "expiryMonth": card.expiry_month, "expiryYear": card.expiry_year, "ccv": card.ccv,
            },
            "creditCardHolderInfo": {
                "name": holder.name, "email": holder.email, "cpfCnpj": holder.cpf_cnpj,
                "postalCode": holder.postal_code, "addressNumber": holder.address_number,
                "phone": holder.phone,
            },
        }
        base = API_BASE_URLS[self.config.environment]
        try:
            response = self.client.request(
                "POST", f"{base}/payments/{payment_id}/payWithCreditCard",
                headers={"accept": "application/json", "user-agent": "FuturaNext/1.0",
                         "access_token": self.config.api_key},
                json=body, follow_redirects=False, timeout=30,
            )
        except httpx.RequestError:
            raise AsaasUncertainCreation("O resultado do pagamento com cartão é incerto.") from None
        if response.status_code == 400:
            raise AsaasCardRefused(_refusal(response), 400)
        if not response.is_success:
            if response.status_code in {401, 403, 404, 429}:
                raise AsaasUnavailable(
                    "O Asaas está indisponível para esta operação.", response.status_code,
                )
            raise AsaasUncertainCreation(
                "O resultado do pagamento com cartão é incerto.", response.status_code,
            )
        try:
            data = response.json(parse_float=Decimal)
        except (ValueError, DecimalException):
            raise AsaasUncertainCreation("Resposta inválida do pagamento com cartão.") from None
        return self._charge(data)

    def create_subscription(self, request: SubscriptionRequest) -> AsaasSubscription:
        """Cria a assinatura mensal. Em resultado incerto, concilie com find_subscription."""
        _check_reference(request.customer, request.external_reference)
        payload = {
            "customer": request.customer,
            "billingType": "UNDEFINED",
            "value": _request_value(ChargeRequest(
                request.customer, "UNDEFINED", request.amount_cents, request.next_due_date,
                request.description, request.external_reference,
            )),
            "nextDueDate": request.next_due_date.isoformat(),
            "cycle": "MONTHLY",
            "description": request.description.strip()[:500],
            "externalReference": request.external_reference,
        }
        try:
            subscription = self._subscription(self._request("POST", "/subscriptions", json=payload))
        except AsaasReconciliationError:
            raise AsaasUncertainCreation("Não foi possível confirmar a assinatura criada.") from None
        if (
            subscription.customer != request.customer
            or subscription.external_reference != request.external_reference
            or subscription.amount_cents != request.amount_cents
        ):
            raise AsaasUncertainCreation("A assinatura retornada diverge da solicitação.")
        return subscription

    def find_subscription(self, customer: str, external_reference: str) -> AsaasSubscription | None:
        _check_reference(customer, external_reference)
        page = self._request(
            "GET", "/subscriptions",
            params={"customer": customer, "externalReference": external_reference, "offset": 0, "limit": 2},
        )
        results = page.get("data")
        if not isinstance(results, list) or type(page.get("hasMore")) is not bool:
            raise AsaasReconciliationError("Listagem de assinaturas do Asaas inválida.")
        found = [item for item in (self._subscription(result) for result in results) if not item.deleted]
        if page["hasMore"] or len(found) > 1:
            raise AsaasReconciliationError("Há mais de uma assinatura para a mesma empresa.")
        if found and (found[0].customer != customer or found[0].external_reference != external_reference):
            raise AsaasReconciliationError("A assinatura retornada não corresponde à empresa.")
        return found[0] if found else None

    def update_subscription(
        self, subscription_id: str, *, amount_cents: int | None = None, active: bool | None = None,
        next_due_date: date | None = None,
    ) -> AsaasSubscription:
        """Altera valor ou situação. INACTIVE pausa a assinatura sem apagar as faturas já geradas."""
        if not SUBSCRIPTION_ID.fullmatch(subscription_id):
            raise AsaasRejected("Identificador de assinatura Asaas inválido.")
        payload: dict = {}
        if amount_cents is not None:
            if type(amount_cents) is not int or amount_cents <= 0:
                raise AsaasRejected("Informe um valor positivo em centavos de real.")
            payload["value"] = amount_cents / 100
            # Atualiza também as cobranças já geradas e ainda não pagas.
            payload["updatePendingPayments"] = True
        if active is not None:
            payload["status"] = "ACTIVE" if active else "INACTIVE"
        if next_due_date is not None:
            payload["nextDueDate"] = next_due_date.isoformat()
        subscription = self._subscription(
            self._request("PUT", f"/subscriptions/{subscription_id}", json=payload)
        )
        if subscription.id != subscription_id:
            raise AsaasReconciliationError("O identificador retornado diverge da assinatura.")
        return subscription

    def get_subscription(self, subscription_id: str) -> AsaasSubscription:
        if not SUBSCRIPTION_ID.fullmatch(subscription_id):
            raise AsaasRejected("Identificador de assinatura Asaas inválido.")
        subscription = self._subscription(self._request("GET", f"/subscriptions/{subscription_id}"))
        if subscription.id != subscription_id:
            raise AsaasReconciliationError("O identificador retornado diverge da assinatura.")
        return subscription

    def _subscription(self, data: object) -> AsaasSubscription:
        try:
            if not isinstance(data, dict):
                raise TypeError
            subscription_id, customer = data["id"], data["customer"]
            status, due_date = data["status"], data["nextDueDate"]
            reference = data.get("externalReference")
            deleted = data.get("deleted", False)
            if (
                not isinstance(subscription_id, str) or not SUBSCRIPTION_ID.fullmatch(subscription_id)
                or not isinstance(customer, str) or not CUSTOMER_ID.fullmatch(customer)
                or not isinstance(status, str) or not PAYMENT_STATUS.fullmatch(status)
                or not isinstance(due_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date)
                or (reference is not None and not isinstance(reference, str))
                or type(deleted) is not bool
            ):
                raise ValueError
            return AsaasSubscription(
                subscription_id, customer, _amount_cents(data["value"]), date.fromisoformat(due_date),
                status, reference, deleted,
            )
        except (TypeError, KeyError, ValueError, OverflowError, DecimalException):
            raise AsaasReconciliationError(
                "Os dados da assinatura retornada pelo Asaas são inválidos."
            ) from None

    def get_customer(self, customer_id: str) -> AsaasCustomer:
        if not CUSTOMER_ID.fullmatch(customer_id):
            raise AsaasRejected("Identificador de cliente Asaas inválido.")
        customer = self._customer(self._request("GET", f"/customers/{customer_id}"))
        if customer.id != customer_id:
            raise AsaasReconciliationError("O identificador retornado diverge do cliente.")
        return customer

    def disable_notifications(self, customer_id: str) -> AsaasCustomer:
        if not CUSTOMER_ID.fullmatch(customer_id):
            raise AsaasRejected("Identificador de cliente Asaas inválido.")
        customer = self._customer(self._request(
            "POST", f"/customers/{customer_id}", json={"notificationDisabled": True},
        ))
        if customer.id != customer_id or not customer.notification_disabled:
            raise AsaasReconciliationError("O Asaas não confirmou a desativação das notificações.")
        return customer

    def _customer(self, data: object) -> AsaasCustomer:
        try:
            if not isinstance(data, dict):
                raise TypeError
            customer_id = data["id"]
            reference = data.get("externalReference")
            disabled = data.get("notificationDisabled", False)
            deleted = data.get("deleted", False)
            if (
                not isinstance(customer_id, str) or not CUSTOMER_ID.fullmatch(customer_id)
                or (reference is not None and not isinstance(reference, str))
                or type(disabled) is not bool or type(deleted) is not bool
            ):
                raise ValueError
            return AsaasCustomer(customer_id, reference, disabled, deleted)
        except (TypeError, KeyError, ValueError):
            raise AsaasReconciliationError(
                "Os dados do cliente retornado pelo Asaas são inválidos."
            ) from None

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
            reference, due_date = data.get("externalReference"), data["dueDate"]
            subscription = data.get("subscription")
            amount = _amount_cents(data["value"])
            original = data.get("originalValue")
            deleted = data.get("deleted", False)
            invoice = hosted_invoice_url(data.get("invoiceUrl"), self.config.environment)
            if (
                not PAYMENT_ID.fullmatch(payment_id)
                or not CUSTOMER_ID.fullmatch(customer)
                or method not in BILLING_TYPES
                or not PAYMENT_STATUS.fullmatch(status)
                or (subscription is not None and not (
                    isinstance(subscription, str) and SUBSCRIPTION_ID.fullmatch(subscription)
                ))
                # Cobranças avulsas sempre têm referência; as da assinatura podem vir sem ela.
                or (reference is None and subscription is None)
                or (reference is not None and not (
                    isinstance(reference, str) and REFERENCE.fullmatch(reference)
                ))
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
                bank_slip_url(data.get("bankSlipUrl"), self.config.environment),
                _challenge_url(data.get("threeDSecureChallengeUrl")),
                subscription,
            )
        except (TypeError, KeyError, ValueError, OverflowError, DecimalException):
            raise AsaasReconciliationError(
                "Os dados da cobrança retornada pelo Asaas são inválidos."
            ) from None
