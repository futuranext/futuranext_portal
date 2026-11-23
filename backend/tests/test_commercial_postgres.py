"""Assinatura mensal (liga/desliga), faturas da assinatura via webhook, compra de ferramentas e teste grátis."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_billing_postgres import WEBHOOK_SECRET, SimulatedAsaas
from test_postgres import Platform, add_auth_user, confirm_membership, email_for
from test_postgres import platform as postgres_platform  # noqa: F401

from app.auth import Identity
from app.dependencies import authenticate, get_database
from app.main import create_app
from app.modules.asaas import AsaasClient, AsaasConfig
from app.repository import Repository
from app.settings import Settings


class SubscriptionAsaas(SimulatedAsaas):
    def __init__(self):
        super().__init__()
        self.subscriptions: dict[str, dict] = {}
        self.subscription_posts: list[dict] = []
        self.subscription_updates: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith("/v3/subscriptions"):
            return super().handle(request)
        if request.method == "POST":
            data = json.loads(request.content)
            self.subscription_posts.append(data)
            item = {**data, "id": f"sub_{self.suffix}_{len(self.subscription_posts)}",
                    "status": "ACTIVE", "deleted": False}
            self.subscriptions[item["id"]] = item
            return httpx.Response(200, json=item)
        if path == "/v3/subscriptions":
            data = [item for item in self.subscriptions.values()
                    if item["customer"] == request.url.params.get("customer")
                    and item["externalReference"] == request.url.params.get("externalReference")]
            return httpx.Response(200, json={"hasMore": False, "data": data})
        item = self.subscriptions[path.rsplit("/", 1)[-1]]
        if request.method == "PUT":
            data = json.loads(request.content)
            self.subscription_updates.append(data)
            item.update({key: value for key, value in data.items() if key in {"value", "nextDueDate", "status"}})
        return httpx.Response(200, json=item)

    def subscription_charge(self, subscription_id: str, due: date, status: str = "PENDING") -> str:
        item = self.subscriptions[subscription_id]
        payment_id = f"pay_sub_{self.suffix}_{len(self.charges) + 1}"
        self.charges[payment_id] = {
            "id": payment_id, "customer": item["customer"], "billingType": "UNDEFINED",
            "value": item["value"], "dueDate": due.isoformat(), "status": status,
            "subscription": subscription_id, "externalReference": None,
            "invoiceUrl": f"https://sandbox.asaas.com/i/{payment_id}",
            "bankSlipUrl": f"https://sandbox.asaas.com/b/pdf/{payment_id}",
        }
        return payment_id


class FakeAuth:
    def __init__(self):
        self.invites: list[tuple[str, str, str | None]] = []

    def send_invitation(self, email, redirect_url, *, full_name=None):
        self.invites.append((email, redirect_url, full_name))
        return "sent"


@dataclass
class Commerce:
    api: TestClient
    asaas: SubscriptionAsaas
    auth: FakeAuth
    platform: Platform
    product: UUID
    member: UUID
    users: set = field(default_factory=set)

    def as_user(self, user: UUID) -> dict:
        return {"X-Test-Actor": str(user)}

    @property
    def admin_billing(self) -> str:
        return f"/api/v1/admin/clients/{self.platform.tenant_a}/billing"


@pytest.fixture(scope="module")
def commerce(postgres_platform: Platform) -> Iterator[Commerce]:  # noqa: F811
    platform = postgres_platform
    slug = f"robo-{platform.suffix}"
    with platform.owner.begin() as connection:
        product = connection.scalar(text("""
            INSERT INTO futuranext.products (slug, name, description, offerable, price_cents)
            VALUES (:slug, 'Robô de testes', 'Ferramenta vendida no portal', true, 4990) RETURNING id
        """), {"slug": slug})
        member = add_auth_user(connection, email_for("carol", platform.suffix))
    confirm_membership(platform.database, platform.admin, member, platform.tenant_a,
                       email_for("carol", platform.suffix))

    server = create_app(Settings(_env_file=None, frontend_url="https://futuranext.test"))
    known = {platform.admin, platform.alice, platform.bob, member}

    def actor(request: Request) -> Identity:
        subject = UUID(request.headers["X-Test-Actor"])
        assert subject in known | commerce_users
        return Identity(subject, None, "aal2", "synthetic")

    commerce_users: set[UUID] = set()
    server.dependency_overrides[authenticate] = actor
    server.dependency_overrides[get_database] = lambda: platform.database
    asaas = SubscriptionAsaas()
    auth = FakeAuth()
    with (
        httpx.Client(transport=httpx.MockTransport(asaas.handle)) as transport,
        TestClient(server) as client,
    ):
        server.state.asaas = AsaasClient(transport, AsaasConfig("sandbox-mock", webhook_token=WEBHOOK_SECRET))
        server.state.auth = auth
        server.state.database = platform.database
        flow = Commerce(client, asaas, auth, platform, product, member, commerce_users)
        response = client.put(flow.admin_billing + "/account", headers=flow.as_user(platform.admin), json={
            "customer_id": f"cus_{platform.suffix}",
        })
        assert response.status_code == 200, response.text
        try:
            yield flow
        finally:
            # Não deixa a ferramenta de teste aparecer nas ofertas de outros módulos.
            with platform.owner.begin() as connection:
                connection.execute(text("UPDATE futuranext.products SET active = false WHERE id = :id"),
                                   {"id": product})


def random_cnpj() -> str:
    """CNPJ válido e diferente a cada execução (o teste grátis vale uma vez por documento)."""
    digits = [int(char) for char in f"{uuid4().int % 10**8:08d}0001"]
    for weights in ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]):
        rest = sum(d * w for d, w in zip(digits, weights, strict=True)) % 11
        digits.append(0 if rest < 2 else 11 - rest)
    return "".join(map(str, digits))


def webhook(flow: Commerce, payment_id: str, subscription_id: str, event: str = "PAYMENT_CREATED"):
    return flow.api.post("/api/v1/integrations/asaas/events", headers={"asaas-access-token": WEBHOOK_SECRET},
                         json={"id": f"evt_{uuid4().hex}", "event": event,
                               "payment": {"id": payment_id, "subscription": subscription_id}})


def test_admin_turns_monthly_billing_on_and_off(commerce: Commerce):
    admin = commerce.as_user(commerce.platform.admin)
    due = date.today() + timedelta(days=10)
    no_service = commerce.api.put(commerce.admin_billing + "/subscription", headers=admin,
                                  json={"enabled": True, "next_due_date": due.isoformat()})
    assert no_service.status_code == 409

    service = commerce.api.post(commerce.admin_billing + "/services", headers=admin,
                                json={"name": "Suporte mensal", "amount_cents": 5000, "active": True})
    assert service.status_code == 201
    past = commerce.api.put(commerce.admin_billing + "/subscription", headers=admin,
                            json={"enabled": True, "next_due_date": "2020-01-01"})
    assert past.status_code == 422

    enabled = commerce.api.put(commerce.admin_billing + "/subscription", headers=admin,
                               json={"enabled": True, "next_due_date": due.isoformat()})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["subscription"] == {
        "enabled": True, "status": "active", "value_cents": 5000, "next_due_date": due.isoformat(),
    }
    created = commerce.asaas.subscription_posts[-1]
    assert created["billingType"] == "UNDEFINED" and created["cycle"] == "MONTHLY"
    assert created["value"] == 50.0 and created["externalReference"] == f"futuranext:subscription:{commerce.platform.tenant_a}"

    # Repetir a ação não cria outra assinatura no Asaas.
    again = commerce.api.put(commerce.admin_billing + "/subscription", headers=admin,
                             json={"enabled": True, "next_due_date": due.isoformat()})
    assert again.status_code == 200 and len(commerce.asaas.subscription_posts) == 1

    extra = commerce.api.post(commerce.admin_billing + "/services", headers=admin,
                              json={"name": "Hospedagem", "amount_cents": 2000, "active": True})
    assert extra.status_code == 201
    assert commerce.asaas.subscription_updates[-1] == {"value": 70.0, "updatePendingPayments": True}

    paused = commerce.api.put(commerce.admin_billing + "/subscription", headers=admin, json={"enabled": False})
    assert paused.json()["subscription"]["status"] == "paused"
    assert commerce.asaas.subscription_updates[-1] == {"status": "INACTIVE"}

    resumed = commerce.api.put(commerce.admin_billing + "/subscription", headers=admin,
                               json={"enabled": True, "next_due_date": due.isoformat()})
    assert resumed.json()["subscription"]["status"] == "active"
    assert commerce.asaas.subscription_updates[-1] == {"status": "ACTIVE", "nextDueDate": due.isoformat()}

    # Cliente não liga nem desliga a cobrança.
    denied = commerce.api.put(commerce.admin_billing + "/subscription", headers=commerce.as_user(commerce.platform.alice),
                              json={"enabled": False})
    assert denied.status_code == 403


def test_subscription_charges_become_portal_invoices(commerce: Commerce):
    subscription_id = next(iter(commerce.asaas.subscriptions))
    due = date.today() + timedelta(days=40)
    payment_id = commerce.asaas.subscription_charge(subscription_id, due)
    assert webhook(commerce, payment_id, subscription_id).status_code == 200
    assert webhook(commerce, payment_id, subscription_id).status_code == 200  # repetido: idempotente

    client = commerce.as_user(commerce.platform.alice)
    portal = commerce.api.get(f"/api/v1/tenants/{commerce.platform.tenant_a}/billing", headers=client).json()
    invoices = [item for item in portal["invoices"] if item["due_date"] == due.isoformat()]
    assert len(invoices) == 1 and invoices[0]["status"] == "open" and invoices[0]["total_cents"] == 7000
    assert portal["subscription"]["status"] == "active"

    pix = commerce.api.post(
        f"/api/v1/tenants/{commerce.platform.tenant_a}/billing/invoices/{invoices[0]['id']}/payment/pix",
        headers=client,
    )
    assert pix.status_code == 200, pix.text

    commerce.asaas.charges[payment_id]["status"] = "RECEIVED"
    assert webhook(commerce, payment_id, subscription_id, "PAYMENT_RECEIVED").status_code == 200
    paid = commerce.api.get(f"/api/v1/tenants/{commerce.platform.tenant_a}/billing", headers=client).json()
    assert [item["status"] for item in paid["invoices"] if item["id"] == invoices[0]["id"]] == ["paid"]

    # Evento de assinatura desconhecida é ignorado.
    stranger = commerce.asaas.subscription_charge(subscription_id, due + timedelta(days=30))
    commerce.asaas.charges[stranger]["subscription"] = "sub_desconhecida"
    assert webhook(commerce, stranger, "sub_desconhecida").status_code == 200
    unchanged = commerce.api.get(f"/api/v1/tenants/{commerce.platform.tenant_a}/billing", headers=client).json()
    assert len(unchanged["invoices"]) == len(paid["invoices"])


def test_company_admin_buys_tool_and_price_joins_subscription(commerce: Commerce):
    tenant = commerce.platform.tenant_a
    alice = commerce.as_user(commerce.platform.alice)
    offers = commerce.api.get(f"/api/v1/tenants/{tenant}/offers", headers=alice).json()
    offer = next(item for item in offers if item["id"] == str(commerce.product))
    assert offer["price_cents"] == 4990

    denied = commerce.api.post(f"/api/v1/tenants/{tenant}/offers/{commerce.product}/purchase",
                               headers=commerce.as_user(commerce.member))
    assert denied.status_code == 403

    bought = commerce.api.post(f"/api/v1/tenants/{tenant}/offers/{commerce.product}/purchase", headers=alice)
    assert bought.status_code == 200, bought.text
    assert bought.json()["subscription"]["value_cents"] == 7000 + 4990
    assert commerce.asaas.subscription_updates[-1] == {"value": 119.9, "updatePendingPayments": True}

    products = commerce.api.get(f"/api/v1/tenants/{tenant}/products", headers=alice).json()
    assert str(commerce.product) in {item["id"] for item in products}
    offers = commerce.api.get(f"/api/v1/tenants/{tenant}/offers", headers=alice).json()
    assert str(commerce.product) not in {item["id"] for item in offers}
    # Comprar de novo não duplica o serviço.
    assert commerce.api.post(f"/api/v1/tenants/{tenant}/offers/{commerce.product}/purchase",
                             headers=alice).json()["subscription"]["value_cents"] == 11990


def test_public_signup_starts_one_trial_per_document(commerce: Commerce):
    email = f"dono-{commerce.platform.suffix}@example.test"
    document = random_cnpj()
    body = {"full_name": "Dona Teste", "email": email, "company": "Clínica Nova Ltda",
            "document": f"{document[:2]}.{document[2:5]}.{document[5:8]}/{document[8:12]}-{document[12:]}", "phone": "(19) 98888-7777", "accept_terms": True}
    created = commerce.api.post("/api/v1/public/signup", json=body)
    assert created.status_code == 201, created.text
    assert created.json() == {"email": email, "trial_days": 15, "delivery": "sent"}
    assert commerce.auth.invites[-1] == (email, "https://futuranext.test/ativar-conta", "Dona Teste")

    with commerce.platform.owner.begin() as connection:
        org = connection.execute(text("""
            SELECT org.id, org.slug, trial.ends_at, invite.id AS invite, invite.role, invite.delivery
            FROM futuranext.organizations org
            JOIN futuranext.trials trial ON trial.tenant_id = org.id
            JOIN futuranext.invitations invite ON invite.tenant_id = org.id
            WHERE org.document = :document
        """), {"document": document}).mappings().one()
        license_end = connection.scalar(text("""
            SELECT ends_at FROM futuranext.tenant_products WHERE tenant_id = :org AND product_id = :product
        """), {"org": org["id"], "product": commerce.product})
        owner = add_auth_user(connection, email)
    assert org["role"] == "cliente_admin" and org["delivery"] == "sent" and org["slug"].startswith("clinica-nova-ltda-")
    assert abs(org["ends_at"] - (datetime.now(UTC) + timedelta(days=15))) < timedelta(minutes=5)
    assert license_end == org["ends_at"]

    again = commerce.api.post("/api/v1/public/signup", json={**body, "email": "outro@example.test"})
    assert again.status_code == 409
    assert commerce.api.post("/api/v1/public/signup", json={**body, "document": "111.111.111-11"}).status_code == 422
    assert commerce.api.post("/api/v1/public/signup", json={**body, "accept_terms": False}).status_code == 422
    # Três cadastros válidos por hora e por visitante; os recusados na validação não contam.
    assert commerce.api.post("/api/v1/public/signup", json=body).status_code == 409
    assert commerce.api.post("/api/v1/public/signup", json=body).status_code == 429

    with commerce.platform.database.session(owner, "aal1") as connection:
        assert Repository(connection).accept_invitation(org["invite"], email)
    commerce.users.add(owner)
    plan = commerce.api.get(f"/api/v1/tenants/{org['id']}/plan", headers=commerce.as_user(owner)).json()
    assert plan["trial_active"] is True and plan["subscription"] is None
    tools = commerce.api.get(f"/api/v1/tenants/{org['id']}/products", headers=commerce.as_user(owner)).json()
    assert str(commerce.product) in {item["id"] for item in tools}
    # Durante o teste a ferramenta continua à venda; a primeira mensalidade vence no fim do teste.
    offers = commerce.api.get(f"/api/v1/tenants/{org['id']}/offers", headers=commerce.as_user(owner)).json()
    assert str(commerce.product) in {item["id"] for item in offers}


def test_signup_unavailable_without_database():
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.post("/api/v1/public/signup", json={
            "full_name": "Ana", "email": "ana@example.com", "company": "ACME", "document": "11222333000181",
            "phone": "11988887777", "accept_terms": True,
        })
    assert response.status_code == 503
