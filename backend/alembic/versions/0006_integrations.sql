CREATE TABLE futuranext.whatsapp_webhooks (
    tenant_id uuid PRIMARY KEY REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    instance_id uuid NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE futuranext.whatsapp_events (
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    instance_id uuid NOT NULL,
    event_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('message.received', 'message.status', 'instance.connection')),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, instance_id, event_id)
);

CREATE INDEX whatsapp_events_recent ON futuranext.whatsapp_events
    (tenant_id, instance_id, received_at DESC, event_id);

DO $$
DECLARE
    relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['whatsapp_webhooks', 'whatsapp_events'] LOOP
        EXECUTE format('ALTER TABLE futuranext.%I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE futuranext.%I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format(
            'CREATE POLICY owner_operations ON futuranext.%I TO %I USING (true) WITH CHECK (true)',
            relation, current_user
        );
    END LOOP;
END
$$;

CREATE POLICY whatsapp_settings_read ON futuranext.whatsapp_webhooks FOR SELECT TO fn_app
    USING (futuranext.can_use_tool(tenant_id, 'whatsapp', false));

CREATE POLICY whatsapp_settings_insert ON futuranext.whatsapp_webhooks FOR INSERT TO fn_app
    WITH CHECK (futuranext.can_use_tool(tenant_id, 'whatsapp', true));

CREATE POLICY whatsapp_settings_update ON futuranext.whatsapp_webhooks FOR UPDATE TO fn_app
    USING (futuranext.can_use_tool(tenant_id, 'whatsapp', true))
    WITH CHECK (futuranext.can_use_tool(tenant_id, 'whatsapp', true));

CREATE POLICY whatsapp_events_read ON futuranext.whatsapp_events FOR SELECT TO fn_app
    USING (futuranext.can_use_tool(tenant_id, 'whatsapp', false));

CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON futuranext.whatsapp_webhooks
    FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change();

REVOKE ALL ON TABLE futuranext.whatsapp_webhooks, futuranext.whatsapp_events FROM PUBLIC;
GRANT SELECT, INSERT ON TABLE futuranext.whatsapp_webhooks TO fn_app;
GRANT UPDATE (instance_id, enabled, updated_at) ON TABLE futuranext.whatsapp_webhooks TO fn_app;
GRANT SELECT ON TABLE futuranext.whatsapp_events TO fn_app;

-- A API valida a assinatura antes de chamar esta rotina. A licença é reavaliada no banco.
CREATE FUNCTION futuranext.register_whatsapp_event(
    org uuid, source_instance uuid, item uuid, event_kind text, event_time timestamptz
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
    PERFORM 1 FROM futuranext.whatsapp_webhooks AS hook
        JOIN futuranext.organizations AS organization ON organization.id = hook.tenant_id
        JOIN futuranext.tenant_products AS license ON license.tenant_id = organization.id
        JOIN futuranext.products AS product ON product.id = license.product_id
        WHERE hook.tenant_id = org AND hook.enabled AND hook.instance_id = source_instance
          AND organization.active AND product.active AND product.slug = 'whatsapp'
          AND license.active AND license.starts_at <= now()
          AND (license.ends_at IS NULL OR license.ends_at > now())
        FOR SHARE OF hook, organization, license, product;
    IF NOT FOUND THEN
        RETURN 'unavailable';
    END IF;

    INSERT INTO futuranext.whatsapp_events (tenant_id, instance_id, event_id, kind, occurred_at)
    VALUES (org, source_instance, item, event_kind, event_time)
    ON CONFLICT DO NOTHING;
    IF FOUND THEN
        RETURN 'accepted';
    END IF;

    IF EXISTS (
        SELECT 1 FROM futuranext.whatsapp_events AS event
        WHERE event.tenant_id = org AND event.instance_id = source_instance AND event.event_id = item
          AND event.kind = event_kind AND event.occurred_at = event_time
    ) THEN
        RETURN 'duplicate';
    END IF;
    RETURN 'conflict';
END;
$$;

REVOKE ALL ON FUNCTION futuranext.register_whatsapp_event(uuid, uuid, uuid, text, timestamptz)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION futuranext.register_whatsapp_event(uuid, uuid, uuid, text, timestamptz)
    TO fn_app;
