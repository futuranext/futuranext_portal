import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.modules.asaas import (
    AsaasClient,
    AsaasConfig,
    AsaasReconciliationError,
    AsaasRejected,
    AsaasUnavailable,
    AsaasUncertainCreation,
    ChargeRequest,
    CustomerRequest,
    hosted_invoice_url,
)

CUSTOMER = "cus_fixture"
REFERENCE = "futuranext:invoice-1"
DUE_DATE = date(2026, 12, 15)
SANDBOX_INVOICE = "https://sandbox.asaas.com/i/fixture"


def provider_charge(**overrides):
    return {
        "id": "pay_fixture",
        "customer": CUSTOMER,
        "billingType": "UNDEFINED",
        "value": 123.45,
        "dueDate": DUE_DATE.isoformat(),
        "externalReference": REFERENCE,
        "status": "PENDING",
        "invoiceUrl": SANDBOX_INVOICE,
        **overrides,
    }


def charge_request(method="UNDEFINED"):
    return ChargeRequest(CUSTOMER, method, 12345, DUE_DATE, " Mensalidade FuturaNext ", REFERENCE)


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("method", ["UNDEFINED", "BOLETO", "PIX", "CREDIT_CARD"])
def test_create_charge_uses_exact_method_amount_and_hosted_invoice(method):
    def provider(request):
        assert request.method == "POST"
        assert request.url == "https://api-sandbox.asaas.com/v3/payments"
        assert request.headers["access_token"] == "synthetic-key"
        assert request.headers["user-agent"] == "FuturaNext/1.0"
        assert json.loads(request.content, parse_float=Decimal) == {
            "customer": CUSTOMER,
            "billingType": method,
            "value": Decimal("123.45"),
            "dueDate": DUE_DATE.isoformat(),
            "description": "Mensalidade FuturaNext",
            "externalReference": REFERENCE,
        }
        return httpx.Response(200, json=provider_charge(billingType=method))

    with mock_client(provider) as transport:
        charge = AsaasClient(transport, AsaasConfig("synthetic-key")).create_charge(
            charge_request(method)
        )
    assert charge.id == "pay_fixture"
    assert charge.amount_cents == 12345
    assert charge.invoice_url == SANDBOX_INVOICE
    assert charge.status == "PENDING"


def test_reconciliation_searches_exact_customer_and_reference_without_creating():
    def provider(request):
        assert request.method == "GET" and not request.content
        assert request.url.path == "/v3/payments"
        assert dict(request.url.params) == {
            "customer": CUSTOMER,
            "externalReference": REFERENCE,
            "offset": "0",
            "limit": "2",
        }
        return httpx.Response(
            200,
            json={
                "hasMore": False,
                "data": [provider_charge(billingType="PIX", status="RECEIVED")],
            },
        )

    with mock_client(provider) as transport:
        charge = AsaasClient(transport, AsaasConfig("synthetic-key")).find_charge(
            CUSTOMER, REFERENCE
        )
    assert charge is not None
    assert charge.status == "RECEIVED" and charge.billing_type == "PIX"


@pytest.mark.parametrize(
    "page",
    [
        {"hasMore": False, "data": [provider_charge(), provider_charge(id="pay_duplicate")]},
        {"hasMore": True, "data": [provider_charge()]},
        {"hasMore": False, "data": [provider_charge(customer="cus_other")]},
        {"hasMore": False, "data": [provider_charge(externalReference="other-reference")]},
        {"data": []},
    ],
)
def test_reconciliation_declines_ambiguous_or_wrongly_filtered_items(page):
    with mock_client(lambda _: httpx.Response(200, json=page)) as transport:
        client = AsaasClient(transport, AsaasConfig("synthetic-key"))
        with pytest.raises(AsaasReconciliationError):
            client.find_charge(CUSTOMER, REFERENCE)


def test_reconciliation_returns_none_for_a_valid_empty_page():
    page = {"hasMore": False, "data": []}
    with mock_client(lambda _: httpx.Response(200, json=page)) as transport:
        client = AsaasClient(transport, AsaasConfig("synthetic-key"))
        assert client.find_charge(CUSTOMER, REFERENCE) is None


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, AsaasRejected),
        (401, AsaasUnavailable),
        (403, AsaasUnavailable),
        (429, AsaasUnavailable),
        (302, AsaasUncertainCreation),
        (500, AsaasUncertainCreation),
    ],
)
def test_creation_reports_errors_without_redirecting_or_resending(status, error):
    calls = []

    def provider(request):
        calls.append(request.method)
        return httpx.Response(status, headers={"Location": "https://wrong.test"})

    with httpx.Client(transport=httpx.MockTransport(provider), follow_redirects=True) as transport:
        client = AsaasClient(transport, AsaasConfig("synthetic-key"))
        with pytest.raises(error):
            client.create_charge(charge_request())
    assert calls == ["POST"]


def test_unconfirmed_creation_requires_reconciliation_without_retry():
    calls = []

    def provider(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with mock_client(provider) as transport:
        client = AsaasClient(transport, AsaasConfig("synthetic-key"))
        with pytest.raises(AsaasUncertainCreation):
            client.create_charge(charge_request())
    assert calls == ["POST"]


@pytest.mark.parametrize(
    "payload",
    [
        provider_charge(value=123.456),
        provider_charge(value=999),
        provider_charge(invoiceUrl="https://example.test/i/fixture"),
        provider_charge(deleted=True),
        {"id": "pay_fixture"},
    ],
)
def test_invalid_creation_response_leaves_the_result_uncertain(payload):
    with mock_client(lambda _: httpx.Response(200, json=payload)) as transport:
        client = AsaasClient(transport, AsaasConfig("synthetic-key"))
        with pytest.raises(AsaasUncertainCreation):
            client.create_charge(charge_request())


@pytest.mark.parametrize(
    "url",
    [
        "http://sandbox.asaas.com/i/fixture",
        "https://sandbox.asaas.com.evil.test/i/fixture",
        "https://name:secret@sandbox.asaas.com/i/fixture",
        "https://sandbox.asaas.com:8080/i/fixture",
        "https://sandbox.asaas.com/redirect?url=https://example.test",
        "https://sandbox.asaas.com/i/fixture?redirect=https://example.test",
        "https://sandbox.asaas.com/i/fixture#extra",
        "https://www.asaas.com/i/fixture",
    ],
)
def test_invoice_url_stays_on_the_correct_environment_and_invoice_path(url):
    assert hosted_invoice_url(url, "sandbox") is None


def test_production_charge_by_payment_id_checks_identity_and_provider_host():
    def provider(request):
        assert request.method == "GET"
        assert request.url == "https://api.asaas.com/v3/payments/pay_fixture"
        return httpx.Response(
            200, json=provider_charge(invoiceUrl="https://www.asaas.com/i/fixture")
        )

    with mock_client(provider) as transport:
        client = AsaasClient(transport, AsaasConfig("synthetic-key", environment="production"))
        assert client.get_charge("pay_fixture").invoice_url == "https://www.asaas.com/i/fixture"


def test_asaas_config_defaults_to_sandbox_and_keeps_secrets_private():
    config = AsaasConfig("synthetic-key", webhook_token="synthetic-webhook-token")
    assert config.environment == "sandbox"
    assert "synthetic" not in repr(config)
    assert config.valid_webhook_token("synthetic-webhook-token")
    assert not config.valid_webhook_token(None)
    assert not config.valid_webhook_token("synthetic-key")


def customer_request(phone="11912345678"):
    return CustomerRequest(
        " Empresa Teste ", "52998224725", "financeiro@example.com", phone, "futuranext:tenant:1",
    )


@pytest.mark.parametrize(("phone", "field"), [("11912345678", "mobilePhone"), ("1132345678", "phone")])
def test_create_customer_sends_required_fields_and_disables_paid_notifications(phone, field):
    def provider(request):
        assert request.method == "POST"
        assert request.url == "https://api-sandbox.asaas.com/v3/customers"
        body = json.loads(request.content)
        assert body == {
            "name": "Empresa Teste", "cpfCnpj": "52998224725", "email": "financeiro@example.com",
            field: phone, "externalReference": "futuranext:tenant:1", "notificationDisabled": True,
        }
        return httpx.Response(200, json={**body, "id": "cus_new", "deleted": False})

    with mock_client(provider) as transport:
        customer = AsaasClient(transport, AsaasConfig("synthetic-key")).create_customer(
            customer_request(phone)
        )
    assert customer.id == "cus_new" and customer.notification_disabled


def test_find_customer_by_company_reference_declines_duplicates():
    def provider(request):
        assert request.method == "GET"
        assert request.url.params["externalReference"] == "futuranext:tenant:1"
        item = {"id": "cus_a", "externalReference": "futuranext:tenant:1", "deleted": False}
        return httpx.Response(200, json={"hasMore": False, "data": [item, {**item, "id": "cus_b"}]})

    with mock_client(provider) as transport, pytest.raises(AsaasReconciliationError):
        AsaasClient(transport, AsaasConfig("synthetic-key")).find_customer("futuranext:tenant:1")


def test_customer_creation_rejection_is_reported():
    rejected = mock_client(lambda request: httpx.Response(400, json={"errors": []}))
    with rejected as transport, pytest.raises(AsaasRejected):
        AsaasClient(transport, AsaasConfig("synthetic-key")).create_customer(customer_request())


def test_bank_slip_url_accepts_only_asaas_pdf_paths():
    from app.modules.asaas import bank_slip_url
    assert bank_slip_url("https://sandbox.asaas.com/b/pdf/abc123", "sandbox") == (
        "https://sandbox.asaas.com/b/pdf/abc123")
    for value in ("https://evil.example/b/pdf/abc", "http://sandbox.asaas.com/b/pdf/abc",
                  "https://sandbox.asaas.com/i/abc", "https://sandbox.asaas.com/b/pdf/abc?x=1"):
        assert bank_slip_url(value, "sandbox") is None
    assert bank_slip_url("https://sandbox.asaas.com/b/pdf/abc", "production") is None


def test_card_refusal_message_comes_from_asaas_and_uncertainty_is_reported():
    from app.modules.asaas import AsaasCardRefused, CardHolder, CreditCard
    card = CreditCard("MARIA", "4111111111111111", "12", "2099", "123")
    holder = CardHolder("MARIA", "m@example.com", "52998224725", "01310100", "100", "11999990000")

    def refused(request):
        return httpx.Response(400, json={"errors": [{"code": "x", "description": "Cartão recusado."}]})

    with mock_client(refused) as transport, pytest.raises(AsaasCardRefused, match="Cartão recusado."):
        AsaasClient(transport, AsaasConfig("synthetic-key")).pay_with_card("pay_x", card, holder)

    def offline(request):
        raise httpx.ReadTimeout("sem resposta", request=request)

    with mock_client(offline) as transport, pytest.raises(AsaasUncertainCreation):
        AsaasClient(transport, AsaasConfig("synthetic-key")).pay_with_card("pay_x", card, holder)
    assert "4111" not in repr(card)
