CREATE EXTENSION IF NOT EXISTS btree_gist;
SET LOCAL search_path = pg_catalog, extensions, public;

CREATE TABLE futuranext.agenda_professionals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    tool_slug text NOT NULL CHECK (tool_slug IN ('barbearia', 'clinica')),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 100),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, tool_slug, id)
);

CREATE TABLE futuranext.agenda_services (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    tool_slug text NOT NULL CHECK (tool_slug IN ('barbearia', 'clinica')),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
    duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 5 AND 480),
    price_cents integer NOT NULL CHECK (price_cents BETWEEN 0 AND 99999999),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, tool_slug, id)
);

CREATE TABLE futuranext.agenda_hours (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    tool_slug text NOT NULL,
    professional_id uuid NOT NULL,
    weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    opens_at time(0) NOT NULL,
    closes_at time(0) NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, tool_slug, professional_id)
        REFERENCES futuranext.agenda_professionals(tenant_id, tool_slug, id),
    CHECK (opens_at < closes_at AND EXTRACT(EPOCH FROM closes_at) < 86400),
    CONSTRAINT agenda_hours_overlap EXCLUDE USING gist (
        tenant_id WITH =, tool_slug WITH =, professional_id WITH =, weekday WITH =,
        int4range((EXTRACT(EPOCH FROM opens_at))::int,
                  (EXTRACT(EPOCH FROM closes_at))::int, '[)') WITH &&
    ) WHERE (active)
);

CREATE INDEX agenda_hours_scope ON futuranext.agenda_hours (tenant_id, tool_slug, professional_id);

CREATE TABLE futuranext.agenda_slots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    tool_slug text NOT NULL,
    professional_id uuid NOT NULL,
    service_id uuid,
    service_price_cents integer CHECK (service_price_cents BETWEEN 0 AND 99999999),
    request_id uuid NOT NULL,
    request_digest text NOT NULL CHECK (length(request_digest) = 64),
    kind text NOT NULL CHECK (kind IN ('appointment', 'block')),
    status text NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    label text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 120),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, tool_slug, professional_id)
        REFERENCES futuranext.agenda_professionals(tenant_id, tool_slug, id),
    FOREIGN KEY (tenant_id, tool_slug, service_id)
        REFERENCES futuranext.agenda_services(tenant_id, tool_slug, id),
    UNIQUE (tenant_id, tool_slug, request_id),
    CHECK (ends_at > starts_at),
    CHECK (
        (kind = 'appointment' AND status IN (
            'scheduled', 'confirmed', 'cancelled', 'completed', 'no_show'
        ) AND service_id IS NOT NULL AND service_price_cents IS NOT NULL)
        OR (kind = 'block' AND status IN ('blocked', 'cancelled')
            AND service_id IS NULL AND service_price_cents IS NULL)
    ),
    CONSTRAINT agenda_slots_overlap EXCLUDE USING gist (
        tenant_id WITH =, tool_slug WITH =, professional_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&
    ) WHERE (status <> 'cancelled')
);

CREATE INDEX agenda_slots_scope ON futuranext.agenda_slots (tenant_id, tool_slug, starts_at);
CREATE INDEX agenda_slots_professional ON futuranext.agenda_slots
    (tenant_id, tool_slug, professional_id, starts_at) WHERE status IN ('scheduled', 'confirmed');

CREATE TABLE futuranext.agenda_conversation (
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    tool_slug text NOT NULL CHECK (tool_slug IN ('barbearia', 'clinica')),
    greeting text NOT NULL CHECK (length(greeting) <= 600),
    confirmation text NOT NULL CHECK (length(confirmation) <= 600),
    cancellation text NOT NULL CHECK (length(cancellation) <= 600),
    handoff text NOT NULL CHECK (length(handoff) <= 600),
    faq text NOT NULL CHECK (length(faq) <= 2000),
    minimum_notice_minutes integer NOT NULL CHECK (minimum_notice_minutes BETWEEN 0 AND 10080),
    booking_horizon_days integer NOT NULL CHECK (booking_horizon_days BETWEEN 1 AND 365),
    cancellation_notice_minutes integer NOT NULL CHECK (
        cancellation_notice_minutes BETWEEN 0 AND 10080
    ),
    PRIMARY KEY (tenant_id, tool_slug)
);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'agenda_professionals', 'agenda_services', 'agenda_hours', 'agenda_slots',
        'agenda_conversation'
    ] LOOP
        EXECUTE format('ALTER TABLE futuranext.%I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('REVOKE ALL ON futuranext.%I FROM PUBLIC', relation);
        EXECUTE format('GRANT SELECT, INSERT ON futuranext.%I TO fn_app', relation);
        EXECUTE format(
            'CREATE POLICY agenda_read ON futuranext.%I FOR SELECT TO fn_app
             USING (futuranext.can_use_tool(tenant_id, tool_slug, false))', relation
        );
        EXECUTE format(
            'CREATE POLICY agenda_create ON futuranext.%I FOR INSERT TO fn_app
             WITH CHECK (futuranext.can_use_tool(tenant_id, tool_slug, true))', relation
        );
        EXECUTE format(
            'CREATE POLICY agenda_update ON futuranext.%I FOR UPDATE TO fn_app
             USING (futuranext.can_use_tool(tenant_id, tool_slug, true))
             WITH CHECK (futuranext.can_use_tool(tenant_id, tool_slug, true))', relation
        );
        EXECUTE format(
            'CREATE TRIGGER audit_change AFTER INSERT OR UPDATE ON futuranext.%I
             FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change()', relation
        );
    END LOOP;
END
$$;

GRANT UPDATE (name, active) ON futuranext.agenda_professionals TO fn_app;
GRANT UPDATE (name, duration_minutes, price_cents, active) ON futuranext.agenda_services TO fn_app;
GRANT UPDATE (weekday, opens_at, closes_at, active) ON futuranext.agenda_hours TO fn_app;
GRANT UPDATE (status, starts_at, ends_at) ON futuranext.agenda_slots TO fn_app;
GRANT UPDATE (greeting, confirmation, cancellation, handoff, faq, minimum_notice_minutes,
    booking_horizon_days, cancellation_notice_minutes) ON futuranext.agenda_conversation TO fn_app;
