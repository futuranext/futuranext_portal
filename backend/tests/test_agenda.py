from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agenda_models import (
    AGENDA_TZ,
    AppointmentCreate,
    AppointmentUpdate,
    HoursInput,
    day_boundary,
    week_range,
)


def test_agenda_normalizes_local_and_offset_times() -> None:
    appointment = {
        "professional_id": uuid4(),
        "service_id": uuid4(),
        "client_name": "Pessoa Teste",
        "request_id": uuid4(),
    }
    local = AppointmentCreate(**appointment, starts_at="2026-11-20T09:00")
    explicit = AppointmentCreate(**appointment, starts_at="2026-11-20T09:00-03:00")
    assert local.starts_at == explicit.starts_at == datetime(2026, 11, 20, 12, tzinfo=UTC)
    assert local.model_dump_json() == explicit.model_dump_json()


def test_agenda_rejects_nonexistent_or_ambiguous_civil_time() -> None:
    with pytest.raises(ValidationError, match="ambíguo ou inexistente"):
        AppointmentUpdate(starts_at="2018-11-04T00:30")
    with pytest.raises(ValidationError, match="ambíguo ou inexistente"):
        AppointmentUpdate(starts_at="2019-02-16T23:30")
    start = day_boundary(date(2018, 11, 4)).astimezone(AGENDA_TZ)
    assert (start.date(), start.hour) == (date(2018, 11, 4), 1)


def test_agenda_checks_hours_changes_and_period_limits() -> None:
    with pytest.raises(ValidationError, match="posterior ao início"):
        HoursInput(weekday=0, opens_at="21:00", closes_at="08:00")
    with pytest.raises(ValidationError, match="horas locais"):
        HoursInput(weekday=0, opens_at="09:30-03:00", closes_at="17:00")
    with pytest.raises(ValidationError, match="por operação"):
        AppointmentUpdate(status="confirmed", starts_at="2026-11-20T09:00")
    with pytest.raises(ValueError, match="1 a 31"):
        week_range(date(2026, 11, 1), date(2026, 12, 3))
    first, last = week_range(None, None)
    assert first.weekday() == 0 and last - first == timedelta(days=7)
