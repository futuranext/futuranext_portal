import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_postgres import Platform
from test_postgres import platform as postgres_platform  # noqa: F401

from app.auth import Identity
from app.dependencies import authenticate, get_database
from app.main import create_app
from app.modules.asaas import AsaasClient, AsaasConfig
from app.settings import Settings

WEBHOOK_SECRET = "teste-local-do-webhook-de-cobranca"


class SimulatedAsaas:
    def __init__(self):
        self.charges = {}
        self.created = []
        self.customers = {}
        self.customer_posts = []
        self.customer_updates = []
        self.reads = 0
        self.timeout_after_charge = False
        self.suffix = uuid4().hex[:8]
        self.released = Event()
        self.started = Event()

    def customer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v3/customers":
            data = json.loads(request.content)
            customer = {**data, "id": f"cus_auto_{self.suffix}_{len(self.customer_posts) + 1}",
                        "deleted": False}
            self.customer_posts.append(data)
            self.customers[customer["id"]] = customer
            return httpx.Response(200, json=customer)
        if path == "/v3/customers":
            reference = request.url.params.get("externalReference")
            data = [item for item in self.customers.values() if item.get("externalReference") == reference]
            return httpx.Response(200, json={"hasMore": False, "data": data})
        customer_id = path.rsplit("/", 1)[-1]
        # IDs vinculados manualmente nos testes já existem no Asaas, com notificações ativas.
        customer = self.customers.setdefault(customer_id, {
            "id": customer_id, "notificationDisabled": False, "deleted": False,
        })
        if request.method == "POST":
            self.customer_updates.append(json.loads(request.content))
            customer.update(json.loads(request.content))
        return httpx.Response(200, json=customer)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api-sandbox.asaas.com"
        if request.url.path.startswith("/v3/customers"):
            return self.customer(request)
        if request.method == "POST":
            data = json.loads(request.content)
            number = len(self.created) + 1
            self.created.append(data)
            self.started.set()
            if self.timeout_after_charge:
                assert self.released.wait(timeout=7)
            charge = {
                **data, "id": f"pay_mock_{self.suffix}_{number}", "status": "PENDING",
                "invoiceUrl": f"https://sandbox.asaas.com/i/mock-{number}",
            }
            self.charges[charge["id"]] = charge
            if self.timeout_after_charge:
                raise httpx.ReadTimeout("Resposta simulada indisponível.", request=request)
            return httpx.Response(200, json=charge)
        self.reads += 1
        if request.url.path == "/v3/payments":
            data = [
                charge for charge in self.charges.values()
                if charge["customer"] == request.url.params.get("customer")
                and charge["externalReference"] == request.url.params.get("externalReference")
            ]
            return httpx.Response(200, json={"hasMore": False, "data": data})
        charge = self.charges[request.url.path.rsplit("/", 1)[-1]]
        return httpx.Response(200, json=charge)


@dataclass
class BillingFlow:
    api: TestClient
    provider: SimulatedAsaas
    platform: Platform

    @property
    def admin(self):
        return {"X-Test-Actor": str(self.platform.admin)}

    @property
    def client(self):
        return {"X-Test-Actor": str(self.platform.alice)}

    @property
    def other(self):
        return {"X-Test-Actor": str(self.platform.bob)}

    @property
    def admin_path(self):
        return f"/api/v1/admin/clients/{self.platform.tenant_a}/billing"

    @property
    def portal_path(self):
        return f"/api/v1/tenants/{self.platform.tenant_a}/billing"

    def add_service(self, name: str, cents: int):
        response = self.api.post(self.admin_path + "/services", headers=self.admin, json={
            "name": name, "amount_cents": cents, "active": True,
        })
        assert response.status_code == 201, response.text
        return response.json()

    def issue(self, period: str):
        response = self.api.post(self.admin_path + "/invoices", headers=self.admin, json={
            "period": period, "due_date": period + "-20",
        })
        assert response.status_code == 201, response.text
        return response.json()

    def pay(self, invoice):
        return self.api.post(
            f"{self.portal_path}/invoices/{invoice['id']}/payment", headers=self.client,
        )


@pytest.fixture
def billing(postgres_platform: Platform) -> Iterator[BillingFlow]:  # noqa: F811
    platform = postgres_platform
    server = create_app(Settings(_env_file=None))

    def actor(request: Request) -> Identity:
        subject = request.headers.get("X-Test-Actor")
        for user in (platform.admin, platform.alice, platform.bob):
            if subject == str(user):
                return Identity(user, None, "aal2", "synthetic")
        raise HTTPException(401, "Sessão de teste ausente.")

    server.dependency_overrides[authenticate] = actor
    server.dependency_overrides[get_database] = lambda: platform.database
    asaas = SimulatedAsaas()
    with (
        httpx.Client(transport=httpx.MockTransport(asaas.handle)) as transport,
        TestClient(server) as client,
    ):
        server.state.asaas = AsaasClient(transport, AsaasConfig("sandbox-mock", webhook_token=WEBHOOK_SECRET))
        flow = BillingFlow(client, asaas, platform)
        response = client.put(flow.admin_path + "/account", headers=flow.admin, json={
            "customer_id": f"cus_{platform.suffix}",
        })
        assert response.status_code == 200, response.text
        yield flow


def test_snapshot_by_company_and_webhook_reconciliation(billing: BillingFlow):
    api, admin_path = billing.api, billing.admin_path
    hosting = billing.add_service("Hospedagem", 12500)
    billing.add_service("API WhatsApp", 4321)
    invoice = billing.issue("2099-01")
    assert invoice["total_cents"] == 16821
    assert {item["name"] for item in invoice["items"]} == {"Hospedagem", "API WhatsApp"}
    assert api.patch(f"{admin_path}/services/{hosting['id']}", headers=billing.admin, json={
        "name": "Hospedagem", "amount_cents": 14900, "active": True,
    }).status_code == 200
    assert api.get(billing.portal_path, headers=billing.other).status_code == 404
    assert api.get(admin_path, headers=billing.client).status_code == 403
    assert api.post(admin_path + "/invoices", headers=billing.admin, json={
        "period": "2099-01", "due_date": "2099-01-20",
    }).status_code == 409
    assert api.put(admin_path + "/account", headers=billing.admin, json={
        "customer_id": f"cus_new_{billing.platform.suffix}",
    }).status_code == 200
    other_account = f"/api/v1/admin/clients/{billing.platform.tenant_b}/billing/account"
    original_customer = f"cus_{billing.platform.suffix}"
    assert api.put(other_account, headers=billing.admin, json={
        "customer_id": original_customer,
    }).status_code == 409

    result = billing.pay(invoice)
    assert result.status_code == 200, result.text
    assert result.json()["payment_url"] == "https://sandbox.asaas.com/i/mock-1"
    assert billing.pay(invoice).json() == result.json()
    assert len(billing.provider.created) == 1
    assert billing.provider.created[0]["billingType"] == "UNDEFINED"
    assert billing.provider.created[0]["value"] == 168.21
    assert billing.provider.created[0]["customer"] == original_customer
    assert api.post(
        f"{admin_path}/invoices/{invoice['id']}/cancel", headers=billing.admin,
    ).status_code == 409

    hook = "/api/v1/integrations/asaas/events"
    charge = next(iter(billing.provider.charges.values()))
    charge["status"] = "RECEIVED"
    charge["value"] = 171.23
    charge["originalValue"] = 168.21
    event = {
        "id": "evt_mock_paid", "event": "PAYMENT_OVERDUE",
        "payment": {"id": charge["id"], "externalReference": charge["externalReference"]},
    }
    assert api.post(hook, json=event).status_code == 401
    headers = {"asaas-access-token": WEBHOOK_SECRET}
    assert api.post(hook, json=event, headers=headers).status_code == 200
    assert api.post(hook, json=event, headers=headers).status_code == 200
    assert billing.provider.reads == 1
    invoices = api.get(billing.portal_path, headers=billing.client).json()["invoices"]
    assert invoices[0]["status"] == "paid"
    assert invoices[0]["total_cents"] == 16821


def test_ambiguous_charge_never_repeats_post_and_can_reconcile(billing: BillingFlow):
    billing.add_service("Serviço", 5999)
    draft = billing.issue("2099-02")
    assert draft["can_cancel"]
    cancelled = billing.api.post(
        f"{billing.admin_path}/invoices/{draft['id']}/cancel", headers=billing.admin,
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    invoice = billing.issue("2099-02")
    assert invoice["id"] != draft["id"]
    billing.provider.timeout_after_charge = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(billing.pay, invoice)
        try:
            assert billing.provider.started.wait(timeout=5)
            second = pool.submit(billing.pay, invoice).result(timeout=5)
            assert second.status_code == 200 and second.json()["status"] == "processing"
        finally:
            billing.provider.released.set()
        assert first.result(timeout=5).json()["status"] == "processing"
    recovered = billing.pay(invoice)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["payment_url"] == "https://sandbox.asaas.com/i/mock-1"
    assert len(billing.provider.created) == 1


def test_customer_is_created_automatically_without_paid_notifications(billing: BillingFlow):
    api, platform = billing.api, billing.platform
    tenant = platform.tenant_b
    admin_path = f"/api/v1/admin/clients/{tenant}/billing"
    company = {"name": "Empresa B", "active": True}
    with platform.owner.begin() as connection:
        connection.execute(text("""
            DELETE FROM futuranext.billing_accounts WHERE tenant_id = :tenant
        """), {"tenant": tenant})

    incomplete = api.get(admin_path, headers=billing.admin).json()
    assert incomplete["customer_status"] == "incomplete" and incomplete["account"] is None
    assert api.patch(f"/api/v1/admin/clients/{tenant}", headers=billing.admin, json={
        **company, "document": "529.982.247-25", "billing_email": "x", "billing_phone": "11912345678",
    }).status_code == 422

    # O portal do próprio cliente também cria o vínculo quando o cadastro está completo.
    with platform.owner.begin() as connection:
        connection.execute(text("""
            UPDATE futuranext.organizations SET document = '52998224725',
                billing_email = 'financeiro@example.com', billing_phone = '11912345678'
            WHERE id = :tenant
        """), {"tenant": tenant})
    portal = api.get(f"/api/v1/tenants/{tenant}/billing", headers=billing.other)
    assert portal.status_code == 200, portal.text
    assert portal.json()["customer_ready"] is True
    assert billing.provider.customer_posts == [{
        "name": "Empresa B", "cpfCnpj": "52998224725", "email": "financeiro@example.com",
        "mobilePhone": "11912345678", "notificationDisabled": True,
        "externalReference": f"futuranext:tenant:{tenant}",
    }]

    linked = api.get(admin_path, headers=billing.admin).json()
    assert linked["customer_status"] == "linked"
    assert linked["account"]["customer_id"].startswith("cus_auto_")
    assert api.get(admin_path, headers=billing.admin).json()["account"] == linked["account"]
    assert len(billing.provider.customer_posts) == 1

    # Editar a empresa atualiza o cliente existente, sem reativar notificações pagas.
    updated = api.patch(f"/api/v1/admin/clients/{tenant}", headers=billing.admin, json={
        **company, "document": "11.222.333/0001-81", "billing_email": "Contas@Example.com",
        "billing_phone": "(11) 3234-5678",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["billing_customer"] == "linked"
    assert billing.provider.customer_updates[-1] == {
        "name": "Empresa B", "cpfCnpj": "11222333000181", "email": "contas@example.com",
        "phone": "1132345678", "notificationDisabled": True,
    }
    assert len(billing.provider.customer_posts) == 1


def test_manual_customer_link_disables_paid_notifications(billing: BillingFlow):
    customer = f"cus_{billing.platform.suffix}"
    assert billing.provider.customers[customer]["notificationDisabled"] is True
    assert {"notificationDisabled": True} in billing.provider.customer_updates
