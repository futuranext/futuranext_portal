CREATE TABLE futuranext.billing_customer_origins (
    environment text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    customer_id text NOT NULL,
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    PRIMARY KEY (environment, customer_id),
    UNIQUE (environment, customer_id, tenant_id)
);

CREATE TABLE futuranext.billing_accounts (
    tenant_id uuid PRIMARY KEY REFERENCES futuranext.organizations(id),
    customer_id text NOT NULL,
    environment text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    FOREIGN KEY (environment, customer_id, tenant_id)
        REFERENCES futuranext.billing_customer_origins(environment, customer_id, tenant_id)
);

CREATE TABLE futuranext.billing_services (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
    amount_cents integer NOT NULL CHECK (amount_cents BETWEEN 1 AND 10000000),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX billing_services_by_tenant ON futuranext.billing_services (tenant_id);

CREATE TABLE futuranext.billing_invoices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
    period date NOT NULL CHECK (EXTRACT(DAY FROM period) = 1),
    due_date date NOT NULL,
    total_cents integer NOT NULL CHECK (total_cents BETWEEN 1 AND 1000000000),
    items jsonb NOT NULL CHECK (
        jsonb_typeof(items) = 'array' AND items <> '[]'::jsonb
        AND NOT jsonb_path_exists(items, '$[*] ? (
            @.type() != "object" || !exists(@.name) || @.name.type() != "string"
            || !exists(@.amount_cents) || @.amount_cents.type() != "number"
            || @.amount_cents < 1 || @.amount_cents > 10000000
            || @.amount_cents != @.amount_cents.floor()
        )')
    ),
    customer_id text NOT NULL,
    environment text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    status text NOT NULL DEFAULT 'open' CHECK (status IN (
        'open', 'paid', 'confirmed', 'overdue', 'processing', 'refunded', 'cancelled', 'review'
    )),
    provider_state text NOT NULL DEFAULT 'unsubmitted' CHECK (provider_state IN (
        'unsubmitted', 'inflight', 'uncertain', 'linked', 'rejected'
    )),
    provider_id text,
    payment_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (environment, provider_id),
    FOREIGN KEY (environment, customer_id, tenant_id)
        REFERENCES futuranext.billing_customer_origins(environment, customer_id, tenant_id)
);

CREATE UNIQUE INDEX billing_one_invoice_per_period ON futuranext.billing_invoices (tenant_id, period)
    WHERE status <> 'cancelled' OR provider_id IS NOT NULL;
CREATE INDEX billing_invoices_by_tenant ON futuranext.billing_invoices (tenant_id, period DESC);

CREATE TABLE futuranext.billing_events (
    environment text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    event_id text NOT NULL,
    invoice_id uuid NOT NULL REFERENCES futuranext.billing_invoices(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (environment, event_id)
);

CREATE INDEX billing_events_by_invoice ON futuranext.billing_events (invoice_id);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'billing_customer_origins', 'billing_accounts', 'billing_services', 'billing_invoices', 'billing_events'
    ] LOOP
        EXECUTE format('ALTER TABLE futuranext.%I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE futuranext.%I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format('REVOKE ALL ON TABLE futuranext.%I FROM PUBLIC', relation);
        EXECUTE format(
            'CREATE POLICY owner_operations ON futuranext.%I TO %I USING (true) WITH CHECK (true)',
            relation, current_user
        );
    END LOOP;

    FOREACH relation IN ARRAY ARRAY['billing_accounts', 'billing_services', 'billing_invoices'] LOOP
        EXECUTE format(
            'CREATE TRIGGER audit_change AFTER INSERT OR UPDATE ON futuranext.%I
             FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change()', relation
        );
        EXECUTE format(
            'CREATE POLICY billing_admin ON futuranext.%I TO fn_app
             USING (tenant_id = futuranext.tenant() AND futuranext.has_permission(''admin.entitlement.manage''))',
            relation
        );
    END LOOP;
END
$$;

CREATE POLICY billing_customer_origin_admin ON futuranext.billing_customer_origins TO fn_app
    USING (tenant_id = futuranext.tenant() AND futuranext.has_permission('admin.entitlement.manage'));

CREATE POLICY billing_invoice_customer_read ON futuranext.billing_invoices FOR SELECT TO fn_app
    USING (tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access'));

CREATE POLICY billing_invoice_customer_payment ON futuranext.billing_invoices FOR UPDATE TO fn_app
    USING (
        tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access')
        AND current_setting('fn.billing_payment', true) = 'true'
    );

-- A aplicação só preenche o ambiente após validar a autenticação do webhook.
CREATE POLICY billing_invoice_webhook_read ON futuranext.billing_invoices FOR SELECT TO fn_app
    USING (
        futuranext.actor() IS NULL AND futuranext.tenant() IS NULL
        AND environment = current_setting('fn.billing_webhook', true)
    );

CREATE POLICY billing_invoice_webhook_update ON futuranext.billing_invoices FOR UPDATE TO fn_app
    USING (
        futuranext.actor() IS NULL AND futuranext.tenant() IS NULL
        AND environment = current_setting('fn.billing_webhook', true)
    );

CREATE POLICY billing_event_webhook_read ON futuranext.billing_events FOR SELECT TO fn_app
    USING (
        futuranext.actor() IS NULL AND futuranext.tenant() IS NULL
        AND environment = current_setting('fn.billing_webhook', true)
    );

CREATE POLICY billing_event_webhook_insert ON futuranext.billing_events FOR INSERT TO fn_app
    WITH CHECK (
        futuranext.actor() IS NULL AND futuranext.tenant() IS NULL
        AND environment = current_setting('fn.billing_webhook', true)
        AND EXISTS (
            SELECT FROM futuranext.billing_invoices AS invoice
            WHERE invoice.id = billing_events.invoice_id AND invoice.environment = billing_events.environment
        )
    );

GRANT SELECT, INSERT ON futuranext.billing_accounts, futuranext.billing_services,
    futuranext.billing_invoices, futuranext.billing_events, futuranext.billing_customer_origins TO fn_app;
GRANT UPDATE (customer_id, environment) ON futuranext.billing_accounts TO fn_app;
GRANT UPDATE (name, amount_cents, active) ON futuranext.billing_services TO fn_app;
GRANT UPDATE (provider_state, provider_id, payment_url, status, updated_at) ON futuranext.billing_invoices TO fn_app;
