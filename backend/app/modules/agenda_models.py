from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    model_validator,
)

from ..schemas import Input

AGENDA_TZ = ZoneInfo("America/Sao_Paulo")
AgendaSlug = Literal["clinica", "barbearia"]
AppointmentStatus = Literal["scheduled", "confirmed", "cancelled", "completed", "no_show"]
SlotStatus = Literal["scheduled", "confirmed", "cancelled", "completed", "no_show", "blocked"]
SlotKind = Literal["appointment", "block"]
StaffName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
ServiceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]
FAQ = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
ACTIVE_APPOINTMENT_STATES = frozenset({"scheduled", "confirmed"})
NEXT_APPOINTMENT_STATES = {
    "scheduled": frozenset({"confirmed", "cancelled", "completed", "no_show"}),
    "confirmed": frozenset({"cancelled", "completed", "no_show"}),
}


def to_utc(value: datetime) -> datetime:
    try:
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC)
        earlier = value.replace(tzinfo=AGENDA_TZ, fold=0).astimezone(UTC)
        later = value.replace(tzinfo=AGENDA_TZ, fold=1).astimezone(UTC)
        if earlier != later or earlier.astimezone(AGENDA_TZ).replace(tzinfo=None) != value:
            raise ValueError("Horário local ambíguo ou inexistente; informe o deslocamento UTC.")
        return earlier
    except OverflowError as error:
        raise ValueError("Data fora do intervalo suportado.") from error


def day_boundary(day: date) -> datetime:
    """Primeiro instante do dia civil, inclusive em transições de horário de verão antigas."""
    midnight = datetime.combine(day, time.min)
    candidates = (midnight.replace(tzinfo=AGENDA_TZ, fold=fold).astimezone(UTC) for fold in (0, 1))
    return min(
        candidate for candidate in candidates if candidate.astimezone(AGENDA_TZ).date() >= day
    )


def same_day_interval(start: datetime, end: datetime) -> tuple[int, time, time] | None:
    local_start, local_end = start.astimezone(AGENDA_TZ), end.astimezone(AGENDA_TZ)
    if local_start.date() != local_end.date() or local_end.time() <= local_start.time():
        return None
    return local_start.weekday(), local_start.time(), local_end.time()


def week_range(begin: date | None, finish: date | None) -> tuple[date, date]:
    today = datetime.now(AGENDA_TZ).date()
    first = begin or today - timedelta(days=today.weekday())
    last = finish or first + timedelta(days=7)
    if not timedelta(days=1) <= last - first <= timedelta(days=31):
        raise ValueError("Escolha um período de 1 a 31 dias.")
    return first, last


def iso_timestamp(value: object) -> object:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not 16 <= len(value) <= 40 or "T" not in value:
        raise ValueError("Informe uma data e hora no formato ISO 8601.")
    return value


LocalOrAwareDatetime = Annotated[
    datetime,
    Field(description="ISO 8601 com deslocamento ou horário local de São Paulo sem deslocamento."),
    BeforeValidator(iso_timestamp),
    AfterValidator(to_utc),
]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]


class ProfessionalInput(Input):
    name: StaffName
    active: bool = True


class Professional(ProfessionalInput):
    id: UUID


class ServiceInput(Input):
    name: ServiceName
    duration_minutes: int = Field(ge=5, le=480)
    price_cents: int = Field(ge=0, le=99999999)
    active: bool = True


class Service(ServiceInput):
    id: UUID


class OfferingInput(Input):
    name: ServiceName
    description: Message = ""
    price_cents: int = Field(ge=0, le=99999999)
    active: bool = True


class Offering(OfferingInput):
    id: UUID


class HoursInput(Input):
    weekday: int = Field(ge=0, le=6, description="Segunda-feira é 0, domingo é 6.")
    opens_at: time
    closes_at: time
    active: bool = True

    @model_validator(mode="after")
    def validate_interval(self) -> "HoursInput":
        times = self.opens_at, self.closes_at
        if any(value.tzinfo or value.microsecond or value.second for value in times):
            raise ValueError("Use horas locais no formato HH:MM.")
        if self.opens_at >= self.closes_at:
            raise ValueError("O fim do expediente deve ser posterior ao início no mesmo dia.")
        return self


class HoursCreate(HoursInput):
    professional_id: UUID


class Hours(HoursCreate):
    id: UUID


class AppointmentCreate(Input):
    professional_id: UUID
    service_id: UUID
    starts_at: LocalOrAwareDatetime
    client_name: Label
    request_id: UUID


class AppointmentUpdate(Input):
    status: AppointmentStatus | None = None
    starts_at: LocalOrAwareDatetime | None = None

    @model_validator(mode="after")
    def requires_change(self) -> "AppointmentUpdate":
        if (self.status is None) == (self.starts_at is None):
            raise ValueError("Informe um novo horário ou estado por operação.")
        return self


class BlockCreate(Input):
    professional_id: UUID
    starts_at: LocalOrAwareDatetime
    ends_at: LocalOrAwareDatetime
    label: Label
    request_id: UUID

    @model_validator(mode="after")
    def requires_range(self) -> "BlockCreate":
        if not timedelta(minutes=1) <= self.ends_at - self.starts_at <= timedelta(days=31):
            raise ValueError("O bloqueio deve durar entre um minuto e 31 dias.")
        return self


class Slot(BaseModel):
    id: UUID
    professional_id: UUID
    service_id: UUID | None
    service_price_cents: int | None
    kind: SlotKind
    status: SlotStatus
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    label: Label


class Conversation(Input):
    greeting: Message = ""
    confirmation: Message = ""
    cancellation: Message = ""
    handoff: Message = ""
    faq: FAQ = ""
    minimum_notice_minutes: int = Field(0, ge=0, le=10080)
    booking_horizon_days: int = Field(90, ge=1, le=365)
    cancellation_notice_minutes: int = Field(0, ge=0, le=10080)


class Metrics(BaseModel):
    scheduled: int = 0
    confirmed: int = 0
    cancelled: int = 0
    completed: int = 0
    no_show: int = 0
    blocked: int = 0
    completed_amount_cents: int = 0


class Snapshot(BaseModel):
    timezone: Literal["America/Sao_Paulo"] = "America/Sao_Paulo"
    range_start: date
    range_end: date
    professionals: list[Professional]
    services: list[Service]
    offerings: list[Offering] = Field(default_factory=list)
    hours: list[Hours]
    slots: list[Slot]
    truncated: bool
    metrics: Metrics
    conversation: Conversation
