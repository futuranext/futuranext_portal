"""Agenda manual e contrato de conversa para estética e barbearia."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from ..dependencies import CurrentUser, Data, tenant_repository
from ..repository import Record
from .access import require_tool
from .agenda_models import (
    ACTIVE_APPOINTMENT_STATES,
    NEXT_APPOINTMENT_STATES,
    AgendaSlug,
    AppointmentCreate,
    AppointmentUpdate,
    BlockCreate,
    Conversation,
    Hours,
    HoursCreate,
    HoursInput,
    Offering,
    OfferingInput,
    Professional,
    ProfessionalInput,
    Service,
    ServiceInput,
    Slot,
    Snapshot,
    week_range,
)
from .agenda_repository import AgendaRepository

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/tools/{tool_slug}/agenda", tags=["Agenda"])


@contextmanager
def scoped_agenda(
    database: Data,
    identity: CurrentUser,
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    *,
    write=False,
) -> Iterator[AgendaRepository]:
    try:
        with tenant_repository(database, identity, tenant_id) as repository:
            require_tool(repository, tenant_id, tool_slug, write=write)
            yield AgendaRepository(repository.connection, tenant_id, tool_slug)
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) != "23P01":
            raise
        raise HTTPException(409, "Este período sobrepõe um horário já registrado.") from error


def found(result: Record | None) -> Record:
    if result is None:
        raise HTTPException(404, "Recurso indisponível.")
    return result


@router.get("", response_model=Snapshot)
def snapshot(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    identity: CurrentUser,
    database: Data,
    begin: Annotated[date | None, Query(alias="from")] = None,
    finish: Annotated[date | None, Query(alias="to")] = None,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug) as agenda:
        try:
            first, last = week_range(begin, finish)
            return agenda.snapshot(first, last)
        except (ValueError, OverflowError) as error:
            raise HTTPException(422, "Escolha um período de 1 a 31 dias.") from error


@router.post("/professionals", response_model=Professional, status_code=201)
def create_professional(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: ProfessionalInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_catalog("professional", payload.model_dump()))


@router.patch("/professionals/{professional_id}", response_model=Professional)
def update_professional(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    professional_id: UUID,
    payload: ProfessionalInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_catalog("professional", payload.model_dump(), professional_id))


@router.post("/services", response_model=Service, status_code=201)
def create_service(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: ServiceInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_catalog("service", payload.model_dump()))


@router.patch("/services/{service_id}", response_model=Service)
def update_service(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    service_id: UUID,
    payload: ServiceInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_catalog("service", payload.model_dump(), service_id))


@router.post("/offerings", response_model=Offering, status_code=201)
def create_offering(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: OfferingInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_catalog("offering", payload.model_dump()))


@router.patch("/offerings/{offering_id}", response_model=Offering)
def update_offering(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    offering_id: UUID,
    payload: OfferingInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_catalog("offering", payload.model_dump(), offering_id))


@router.post("/hours", response_model=Hours, status_code=201)
def create_hours(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: HoursCreate,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        found(agenda.lock_professional(payload.professional_id))
        return found(agenda.save_hours(payload.model_dump()))


@router.patch("/hours/{hours_id}", response_model=Hours)
def update_hours(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    hours_id: UUID,
    payload: HoursInput,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        current = found(agenda.hours(hours_id))
        found(agenda.lock_professional(current["professional_id"]))
        saved = found(agenda.save_hours(payload.model_dump(), hours_id))
        if agenda.future_outside_hours(current["professional_id"]):
            raise HTTPException(
                409, "Remarque os atendimentos futuros antes de alterar este expediente."
            )
        return saved


def booking_end(
    agenda: AgendaRepository,
    professional: UUID,
    start: datetime,
    duration: timedelta,
) -> datetime:
    config = agenda.conversation()
    now = datetime.now(UTC)
    if start < now + timedelta(minutes=config.minimum_notice_minutes):
        raise HTTPException(422, "O início precisa respeitar a antecedência configurada.")
    if start > now + timedelta(days=config.booking_horizon_days):
        raise HTTPException(422, "O início ultrapassa o período de reserva configurado.")
    finish = start + duration
    if not agenda.inside_hours(professional, start, finish):
        raise HTTPException(
            409, "O atendimento inteiro precisa caber no expediente do profissional."
        )
    return finish


def fingerprint(kind: str, payload: AppointmentCreate | BlockCreate) -> str:
    return sha256(f"{kind}:{payload.model_dump_json()}".encode()).hexdigest()


def replay(agenda: AgendaRepository, request: UUID, digest: str) -> Record | None:
    prior = agenda.request_result(request)
    if prior is not None and prior["request_digest"] != digest:
        raise HTTPException(409, "O identificador desta solicitação já foi usado.")
    return prior


@router.post("/appointments", response_model=Slot, status_code=201)
def create_appointment(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: AppointmentCreate,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        professional = found(agenda.lock_professional(payload.professional_id))
        digest = fingerprint("appointment", payload)
        prior = replay(agenda, payload.request_id, digest)
        if prior:
            return prior
        service = found(agenda.lock_service(payload.service_id))
        if not professional["active"] or not service["active"]:
            raise HTTPException(409, "Selecione um profissional e um serviço ativos.")
        ends_at = booking_end(
            agenda,
            payload.professional_id,
            payload.starts_at,
            timedelta(minutes=service["duration_minutes"]),
        )
        saved = agenda.insert_slot(
            {
                "professional_id": payload.professional_id,
                "service_id": payload.service_id,
                "service_price_cents": service["price_cents"],
                "kind": "appointment",
                "status": "scheduled",
                "starts_at": payload.starts_at,
                "ends_at": ends_at,
                "label": payload.client_name,
                "request_id": payload.request_id,
                "request_digest": digest,
            }
        )
        return found(saved or replay(agenda, payload.request_id, digest))


@router.patch("/appointments/{slot_id}", response_model=Slot)
def update_appointment(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    slot_id: UUID,
    payload: AppointmentUpdate,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        existing = found(agenda.slot(slot_id, "appointment"))
        professional_id = existing["professional_id"]
        professional = found(agenda.lock_professional(professional_id))
        current = found(agenda.slot(slot_id, "appointment"))
        old_status = current["status"]
        old_start: datetime = current["starts_at"].astimezone(UTC)
        old_end: datetime = current["ends_at"].astimezone(UTC)
        if payload.starts_at is not None:
            if payload.starts_at == old_start:
                return current
            if old_status not in ACTIVE_APPOINTMENT_STATES or not professional["active"]:
                raise HTTPException(409, "Este atendimento não pode ser remarcado.")
            new_end = booking_end(agenda, professional_id, payload.starts_at, old_end - old_start)
            return found(agenda.update_slot(slot_id, "scheduled", payload.starts_at, new_end))

        status = payload.status
        if status == old_status:
            return current
        if status is None or status not in NEXT_APPOINTMENT_STATES.get(old_status, ()):
            raise HTTPException(409, "A mudança de estado não é permitida.")
        if status in {"completed", "no_show"} and old_start > datetime.now(UTC):
            raise HTTPException(422, "O atendimento ainda não começou.")
        return found(agenda.update_slot(slot_id, status, old_start, old_end))


@router.post("/blocks", response_model=Slot, status_code=201)
def create_block(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: BlockCreate,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        professional = found(agenda.lock_professional(payload.professional_id))
        digest = fingerprint("block", payload)
        prior = replay(agenda, payload.request_id, digest)
        if prior:
            return prior
        if not professional["active"]:
            raise HTTPException(409, "Selecione um profissional ativo.")
        if payload.ends_at <= datetime.now(UTC):
            raise HTTPException(422, "Informe um horário de término futuro.")
        saved = agenda.insert_slot(
            {
                **payload.model_dump(),
                "kind": "block",
                "status": "blocked",
                "service_id": None,
                "service_price_cents": None,
                "request_digest": digest,
            }
        )
        return found(saved or replay(agenda, payload.request_id, digest))


@router.post("/blocks/{slot_id}/cancel", response_model=Slot)
def cancel_block(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    slot_id: UUID,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        existing = found(agenda.slot(slot_id, "block"))
        found(agenda.lock_professional(existing["professional_id"]))
        current = found(agenda.slot(slot_id, "block"))
        return found(
            agenda.update_slot(
                slot_id,
                "cancelled",
                current["starts_at"],
                current["ends_at"],
            )
        )


@router.put("/conversation", response_model=Conversation)
def update_conversation(
    tenant_id: UUID,
    tool_slug: AgendaSlug,
    payload: Conversation,
    identity: CurrentUser,
    database: Data,
):
    with scoped_agenda(database, identity, tenant_id, tool_slug, write=True) as agenda:
        return found(agenda.save_conversation(payload))
