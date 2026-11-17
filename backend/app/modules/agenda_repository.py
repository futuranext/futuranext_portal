from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import Connection

from ..repository import Record, Repository
from .agenda_models import AgendaSlug, Conversation, Metrics, day_boundary, same_day_interval

CatalogKind = Literal["professional", "service", "offering"]
SlotKind = Literal["appointment", "block"]
CATALOG_COLUMNS = {
    "professional": ("agenda_professionals", ("name", "active")),
    "service": ("agenda_services", ("name", "duration_minutes", "price_cents", "active")),
    "offering": ("agenda_offerings", ("name", "description", "price_cents", "active")),
}
HOURS_FIELDS = "id, professional_id, weekday, opens_at, closes_at, active"
SLOT_FIELDS = (
    "id, professional_id, service_id, service_price_cents, kind, status, starts_at, ends_at, label"
)
CONVERSATION_FIELDS = ", ".join(Conversation.model_fields)
CURRENT_TENANT = "tenant_id = :tenant AND tool_slug = :slug"
LIST_LIMIT = 600


class AgendaRepository(Repository):
    def __init__(self, connection: Connection, tenant_id: UUID, slug: AgendaSlug):
        super().__init__(connection)
        self.scope = {"tenant": tenant_id, "slug": slug}

    def catalog(self, kind: CatalogKind) -> list[Record]:
        table, columns = CATALOG_COLUMNS[kind]
        return self._rows(
            f"SELECT id, {', '.join(columns)} FROM futuranext.{table} "
            f"WHERE {CURRENT_TENANT} ORDER BY name, id",
            **self.scope,
        )

    def save_catalog(
        self, kind: CatalogKind, data: Record, item_id: UUID | None = None
    ) -> Record | None:
        table, columns = CATALOG_COLUMNS[kind]
        returning = f"RETURNING id, {', '.join(columns)}"
        if item_id is None:
            bindings = ", ".join(f":{column}" for column in columns)
            return self._one(
                f"INSERT INTO futuranext.{table} (tenant_id, tool_slug, {', '.join(columns)}) "
                f"VALUES (:tenant, :slug, {bindings}) {returning}",
                **self.scope,
                **data,
            )
        assignments = ", ".join(f"{column} = :{column}" for column in columns)
        return self._one(
            f"UPDATE futuranext.{table} SET {assignments} "
            f"WHERE {CURRENT_TENANT} AND id = :id {returning}",
            **self.scope,
            **data,
            id=item_id,
        )

    def lock_professional(self, professional: UUID) -> Record | None:
        return self._one(
            f"SELECT id, active FROM futuranext.agenda_professionals "
            f"WHERE {CURRENT_TENANT} AND id = :id FOR NO KEY UPDATE",
            **self.scope,
            id=professional,
        )

    def lock_service(self, service: UUID) -> Record | None:
        return self._one(
            f"SELECT duration_minutes, price_cents, active FROM futuranext.agenda_services "
            f"WHERE {CURRENT_TENANT} AND id = :id FOR SHARE",
            **self.scope,
            id=service,
        )

    def hours(self, hours_id: UUID) -> Record | None:
        return self._one(
            f"SELECT {HOURS_FIELDS} FROM futuranext.agenda_hours "
            f"WHERE {CURRENT_TENANT} AND id = :id",
            **self.scope,
            id=hours_id,
        )

    def save_hours(self, data: Record, hours_id: UUID | None = None) -> Record | None:
        if hours_id is None:
            return self._one(
                f"""INSERT INTO futuranext.agenda_hours (
                    tenant_id, tool_slug, professional_id, weekday, opens_at, closes_at, active
                ) VALUES (
                    :tenant, :slug, :professional_id, :weekday, :opens_at, :closes_at, :active
                ) RETURNING {HOURS_FIELDS}""",
                **self.scope,
                **data,
            )
        return self._one(
            f"""UPDATE futuranext.agenda_hours SET weekday = :weekday,
                opens_at = :opens_at, closes_at = :closes_at, active = :active
            WHERE {CURRENT_TENANT} AND id = :id
            RETURNING {HOURS_FIELDS}""",
            **self.scope,
            **data,
            id=hours_id,
        )

    def inside_hours(self, professional: UUID, start: datetime, finish: datetime) -> bool:
        interval = same_day_interval(start, finish)
        if interval is None:
            return False
        weekday, opens_at, closes_at = interval
        return bool(
            self._scalar(
                f"""SELECT EXISTS (
                SELECT FROM futuranext.agenda_hours WHERE {CURRENT_TENANT}
                    AND professional_id = :professional AND active AND weekday = :weekday
                    AND opens_at <= :opens_at AND closes_at >= :closes_at
            )""",
                **self.scope,
                professional=professional,
                weekday=weekday,
                opens_at=opens_at,
                closes_at=closes_at,
            )
        )

    def future_outside_hours(self, professional: UUID) -> bool:
        return bool(
            self._scalar(
                f"""SELECT EXISTS (
                SELECT FROM (
                    SELECT starts_at AT TIME ZONE 'America/Sao_Paulo' AS starts_local,
                           ends_at AT TIME ZONE 'America/Sao_Paulo' AS ends_local
                    FROM futuranext.agenda_slots
                    WHERE {CURRENT_TENANT} AND professional_id = :professional
                        AND status IN ('scheduled', 'confirmed') AND starts_at > now()
                ) AS slot
                WHERE NOT EXISTS (
                    SELECT FROM futuranext.agenda_hours WHERE {CURRENT_TENANT}
                        AND active AND professional_id = :professional
                        AND slot.starts_local::date = slot.ends_local::date
                        AND weekday = EXTRACT(ISODOW FROM slot.starts_local)::int - 1
                        AND opens_at <= slot.starts_local::time
                        AND closes_at >= slot.ends_local::time
                )
            )""",
                **self.scope,
                professional=professional,
            )
        )

    def conversation(self) -> Conversation:
        current = self._one(
            f"SELECT {CONVERSATION_FIELDS} FROM futuranext.agenda_conversation "
            f"WHERE {CURRENT_TENANT}",
            **self.scope,
        )
        return Conversation.model_validate(current) if current else Conversation()

    def save_conversation(self, data: Conversation) -> Record | None:
        fields = Conversation.model_fields
        bindings = ", ".join(f":{field}" for field in fields)
        assignments = ", ".join(f"{field} = excluded.{field}" for field in fields)
        return self._one(
            f"""INSERT INTO futuranext.agenda_conversation (
                tenant_id, tool_slug, {CONVERSATION_FIELDS}
            ) VALUES (:tenant, :slug, {bindings}) ON CONFLICT (tenant_id, tool_slug)
            DO UPDATE SET {assignments} RETURNING {CONVERSATION_FIELDS}""",
            **self.scope,
            **data.model_dump(),
        )

    def request_result(self, request_id: UUID) -> Record | None:
        return self._one(
            f"SELECT {SLOT_FIELDS}, request_digest FROM futuranext.agenda_slots "
            f"WHERE {CURRENT_TENANT} AND request_id = :id",
            **self.scope,
            id=request_id,
        )

    def insert_slot(self, data: Record) -> Record | None:
        return self._one(
            f"""INSERT INTO futuranext.agenda_slots (
                tenant_id, tool_slug, professional_id, service_id, service_price_cents, kind,
                status, starts_at, ends_at, label, request_id, request_digest
            ) VALUES (
                :tenant, :slug, :professional_id, :service_id, :service_price_cents, :kind,
                :status, :starts_at, :ends_at, :label, :request_id, :request_digest
            ) ON CONFLICT (tenant_id, tool_slug, request_id) DO NOTHING
            RETURNING {SLOT_FIELDS}""",
            **self.scope,
            **data,
        )

    def slot(self, slot_id: UUID, kind: SlotKind) -> Record | None:
        return self._one(
            f"SELECT {SLOT_FIELDS} FROM futuranext.agenda_slots "
            f"WHERE {CURRENT_TENANT} AND id = :id AND kind = :kind",
            **self.scope,
            id=slot_id,
            kind=kind,
        )

    def update_slot(
        self,
        slot_id: UUID,
        status: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Record | None:
        return self._one(
            f"""UPDATE futuranext.agenda_slots SET status = :status,
                starts_at = :starts_at, ends_at = :ends_at
            WHERE {CURRENT_TENANT} AND id = :id RETURNING {SLOT_FIELDS}""",
            **self.scope,
            id=slot_id,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
        )

    def snapshot(self, first: date, last: date) -> Record:
        begin, end = day_boundary(first), day_boundary(last)
        window = f"{CURRENT_TENANT} AND starts_at < :end AND ends_at > :begin"
        args = self.scope | {"begin": begin, "end": end}
        slots = self._rows(
            f"""SELECT {SLOT_FIELDS}, count(*) OVER () AS total,
                count(*) FILTER (WHERE status = 'scheduled') OVER () AS scheduled,
                count(*) FILTER (WHERE status = 'confirmed') OVER () AS confirmed,
                count(*) FILTER (WHERE status = 'cancelled' AND kind = 'appointment') OVER ()
                    AS cancelled,
                count(*) FILTER (WHERE status = 'completed') OVER () AS completed,
                count(*) FILTER (WHERE status = 'no_show') OVER () AS no_show,
                count(*) FILTER (WHERE status = 'blocked') OVER () AS blocked,
                coalesce(sum(service_price_cents) FILTER (WHERE status = 'completed') OVER (), 0)
                    AS completed_amount_cents
            FROM futuranext.agenda_slots WHERE {window}
            ORDER BY starts_at, id LIMIT :limit""",
            **args,
            limit=LIST_LIMIT,
        )
        metrics = slots[0] if slots else {}
        return {
            "range_start": first,
            "range_end": last,
            "professionals": self.catalog("professional"),
            "services": self.catalog("service"),
            "offerings": self.catalog("offering"),
            "hours": self._rows(
                f"SELECT {HOURS_FIELDS} FROM futuranext.agenda_hours WHERE {CURRENT_TENANT} "
                "ORDER BY weekday, opens_at, id",
                **self.scope,
            ),
            "slots": slots,
            "truncated": metrics.get("total", 0) > LIST_LIMIT,
            "metrics": Metrics.model_validate(metrics),
            "conversation": self.conversation(),
        }
