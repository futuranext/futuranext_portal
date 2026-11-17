from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
import test_postgres
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_postgres import Platform

from app.auth import Identity
from app.dependencies import authenticate, get_database
from app.main import create_app
from app.modules.agenda_models import AGENDA_TZ
from app.repository import Repository
from app.settings import Settings

postgres_platform = test_postgres.platform


@dataclass(frozen=True)
class VerifiedAgenda:
    client: TestClient
    platform: Platform

    def path(self, slug: str = "barbearia", tenant: UUID | None = None) -> str:
        return f"/api/v1/tenants/{tenant or self.platform.tenant_a}/tools/{slug}/agenda"

    @property
    def alice(self) -> dict[str, str]:
        return {"X-Test-Actor": str(self.platform.alice)}

    @property
    def bob(self) -> dict[str, str]:
        return {"X-Test-Actor": str(self.platform.bob)}


@pytest.fixture
def agenda(postgres_platform: Platform) -> Iterator[VerifiedAgenda]:
    platform = postgres_platform
    app = create_app(Settings(_env_file=None))

    def actor(request: Request) -> Identity:
        subject = request.headers.get("X-Test-Actor")
        if subject == str(platform.alice):
            return Identity(platform.alice, None, "aal2", "synthetic")
        if subject == str(platform.bob):
            return Identity(platform.bob, None, "aal1", "synthetic")
        raise HTTPException(401, "Sessão de teste ausente.")

    app.dependency_overrides[authenticate] = actor
    app.dependency_overrides[get_database] = lambda: platform.database
    with TestClient(app) as client:
        yield VerifiedAgenda(client, platform)


def post(agenda: VerifiedAgenda, route: str, payload: dict) -> dict:
    response = agenda.client.post(f"{agenda.path()}{route}", headers=agenda.alice, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_agenda_serializes_concurrent_appointments_and_preserves_hours(
    agenda: VerifiedAgenda,
) -> None:
    client, base = agenda.client, agenda.path()
    tomorrow = datetime.now(AGENDA_TZ).date() + timedelta(days=2)
    professional = post(agenda, "/professionals", {"name": "Profissional de teste"})
    service = post(
        agenda,
        "/services",
        {"name": "Serviço de teste", "duration_minutes": 30, "price_cents": 5500},
    )
    hours_payload = {
        "professional_id": professional["id"],
        "weekday": tomorrow.weekday(),
        "opens_at": "09:00",
        "closes_at": "12:00",
    }
    hours = post(agenda, "/hours", hours_payload)
    overlap = client.post(
        f"{base}/hours",
        headers=agenda.alice,
        json={**hours_payload, "opens_at": "11:00"},
    )
    assert overlap.status_code == 409
    post(agenda, "/hours", {**hours_payload, "opens_at": "14:00", "closes_at": "18:00"})

    reservation = {
        "professional_id": professional["id"],
        "service_id": service["id"],
        "starts_at": f"{tomorrow}T10:00",
        "client_name": "Pessoa Teste",
    }

    def book(request: str):
        return client.post(
            f"{base}/appointments",
            headers=agenda.alice,
            json={
                **reservation,
                "request_id": request,
            },
        )

    attempts = [str(uuid4()), str(uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, attempts))
    failures = [(response.status_code, response.text) for response in results]
    assert sorted(response.status_code for response in results) == [201, 409], failures
    success = next(index for index, response in enumerate(results) if response.status_code == 201)
    slot = results[success].json()
    assert slot["starts_at"] == f"{tomorrow}T13:00:00Z"
    assert slot["ends_at"] == f"{tomorrow}T13:30:00Z"
    assert book(attempts[success]).json()["id"] == slot["id"]
    assert (
        client.post(
            f"{base}/appointments",
            headers=agenda.alice,
            json={
                **reservation,
                "request_id": attempts[success],
                "client_name": "Outro Teste",
            },
        ).status_code
        == 409
    )

    update = client.patch(
        f"{base}/hours/{hours['id']}",
        headers=agenda.alice,
        json={"weekday": tomorrow.weekday(), "opens_at": "09:00", "closes_at": "10:15"},
    )
    assert update.status_code == 409
    block = {
        "professional_id": professional["id"],
        "starts_at": f"{tomorrow}T10:15",
        "ends_at": f"{tomorrow}T10:45",
        "label": "Pausa",
        "request_id": str(uuid4()),
    }
    assert client.post(f"{base}/blocks", headers=agenda.alice, json=block).status_code == 409
    assert (
        client.patch(
            f"{base}/appointments/{slot['id']}",
            headers=agenda.alice,
            json={
                "status": "completed",
            },
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{base}/appointments/{slot['id']}",
            headers=agenda.alice,
            json={
                "status": "cancelled",
            },
        ).json()["status"]
        == "cancelled"
    )
    stored_block = post(agenda, "/blocks", block)
    assert (
        client.post(f"{base}/blocks/{stored_block['id']}/cancel", headers=agenda.alice).json()[
            "status"
        ]
        == "cancelled"
    )
    new_slot = post(agenda, "/appointments", {**reservation, "request_id": str(uuid4())})
    assert new_slot["id"] != slot["id"]

    assert (
        client.patch(
            f"{base}/services/{service['id']}",
            headers=agenda.alice,
            json={"name": "Serviço de teste", "duration_minutes": 45, "price_cents": 7500},
        ).status_code
        == 200
    )
    moved = client.patch(
        f"{base}/appointments/{new_slot['id']}",
        headers=agenda.alice,
        json={
            "starts_at": f"{tomorrow}T11:00",
        },
    ).json()
    assert moved["ends_at"] == f"{tomorrow}T14:30:00Z"
    assert moved["service_price_cents"] == 5500

    summary = client.get(base, params={"from": str(tomorrow)}, headers=agenda.alice).json()
    assert summary["metrics"]["scheduled"] == 1
    assert summary["metrics"]["cancelled"] == 1
    assert summary["metrics"]["blocked"] == 0
    assert summary["timezone"] == "America/Sao_Paulo"
    assert all("request_digest" not in item for item in summary["slots"])


def test_agenda_isolates_companies_and_products_at_api_and_database(agenda: VerifiedAgenda) -> None:
    client, platform = agenda.client, agenda.platform
    barbershop, clinic = agenda.path(), agenda.path("clinica", platform.tenant_b)
    assert client.get(barbershop).status_code == 401
    assert client.get(clinic, headers=agenda.alice).status_code == 404
    assert client.get(barbershop, headers=agenda.bob).status_code == 404
    assert client.get(clinic, headers=agenda.bob).status_code == 200
    assert (
        client.post(
            clinic + "/professionals", headers=agenda.bob, json={"name": "Pessoa Teste"}
        ).status_code
        == 403
    )
    assert client.get(agenda.path("clinica"), headers=agenda.alice).status_code == 404

    barbershop_professional = post(agenda, "/professionals", {"name": "Acesso de teste"})
    post(agenda, "/offerings", {"name": "Produto de teste com isolamento", "price_cents": 1000})
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        assert Repository(connection).upsert_entitlement(
            platform.tenant_a,
            platform.product_b,
            True,
            None,
            None,
        )
    second_module = client.get(agenda.path("clinica"), headers=agenda.alice).json()
    assert second_module["professionals"] == []
    assert second_module["offerings"] == []
    forged = client.post(
        agenda.path("clinica") + "/hours",
        headers=agenda.alice,
        json={
            "professional_id": barbershop_professional["id"],
            "weekday": 0,
            "opens_at": "09:00",
            "closes_at": "10:00",
        },
    )
    assert forged.status_code == 404
    with (
        pytest.raises(DBAPIError) as failure,
        platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection,
    ):
        connection.execute(
            text("""
            INSERT INTO futuranext.agenda_hours
                (tenant_id, tool_slug, professional_id, weekday, opens_at, closes_at)
            VALUES (:tenant, 'clinica', :professional, 0, make_time(9,0,0), make_time(10,0,0))
        """),
            {"tenant": platform.tenant_a, "professional": UUID(barbershop_professional["id"])},
        )
    assert failure.value.orig.sqlstate == "23503"
    with platform.database.session(platform.alice, "aal2", platform.tenant_b) as connection:
        assert connection.scalar(text("SELECT count(*) FROM futuranext.agenda_professionals")) == 0

    updated = client.put(
        barbershop + "/conversation",
        headers=agenda.alice,
        json={
            "greeting": "Olá, como podemos ajudar?",
            "minimum_notice_minutes": 120,
        },
    )
    assert updated.status_code == 200
    assert (
        client.get(barbershop, headers=agenda.alice).json()["conversation"][
            "minimum_notice_minutes"
        ]
        == 120
    )
    assert (
        client.get(agenda.path("clinica"), headers=agenda.alice).json()["conversation"][
            "minimum_notice_minutes"
        ]
        == 0
    )


def test_agenda_offerings_have_an_independent_catalog(agenda: VerifiedAgenda) -> None:
    client, base = agenda.client, agenda.path()
    before = client.get(base, headers=agenda.alice).json()["services"]
    original = post(agenda, "/offerings", {"name": "Produto fictício", "price_cents": 4990})
    assert original["description"] == "" and original["active"]
    updated = client.patch(
        f"{base}/offerings/{original['id']}",
        headers=agenda.alice,
        json={
            "name": original["name"],
            "description": "Descrição de teste",
            "price_cents": 5990,
            "active": False,
        },
    )
    assert updated.status_code == 200
    snapshot = client.get(base, headers=agenda.alice).json()
    assert snapshot["services"] == before
    assert updated.json() in snapshot["offerings"]
    assert updated.json()["description"] == "Descrição de teste"
    assert updated.json()["price_cents"] == 5990 and not updated.json()["active"]
    clinic = agenda.path("clinica", agenda.platform.tenant_b)
    assert client.get(clinic, headers=agenda.bob).json()["offerings"] == []
    assert (
        client.post(
            clinic + "/offerings",
            headers=agenda.bob,
            json={
                "name": "Produto fictício",
                "price_cents": 4990,
            },
        ).status_code
        == 403
    )
