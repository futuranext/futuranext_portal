-- Oferta comercial: preço mensal das ferramentas, compra pelo portal, assinatura recorrente
-- no Asaas (com liga/desliga por empresa) e teste grátis de 15 dias para novos cadastros.

-- 1. Preço mensal da ferramenta. Sem preço, a ferramenta não pode ser comprada pelo portal.
ALTER TABLE futuranext.products
    ADD COLUMN price_cents integer CONSTRAINT products_price_check CHECK (price_cents BETWEEN 1 AND 10000000);
GRANT UPDATE (price_cents) ON futuranext.products TO fn_app;

-- 2. Serviço de cobrança criado pela compra de uma ferramenta (um por ferramenta e empresa).
ALTER TABLE futuranext.billing_services ADD COLUMN product_id uuid REFERENCES futuranext.products(id);
CREATE UNIQUE INDEX billing_service_per_product ON futuranext.billing_services (tenant_id, product_id)
    WHERE product_id IS NOT NULL;

-- 3. Assinatura mensal da empresa no Asaas. "enabled" é a chave de liga/desliga da cobrança.
CREATE TABLE futuranext.billing_subscriptions (
    tenant_id uuid PRIMARY KEY REFERENCES futuranext.organizations(id),
    environment text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    enabled boolean NOT NULL DEFAULT true,
    provider_id text CHECK (provider_id ~ '^sub_[A-Za-z0-9_-]{1,120}$'),
    provider_state text NOT NULL DEFAULT 'unsubmitted' CHECK (provider_state IN (
        'unsubmitted', 'inflight', 'uncertain', 'linked', 'rejected'
    )),
    value_cents integer CHECK (value_cents BETWEEN 1 AND 1000000000),
    next_due_date date,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (environment, provider_id)
);

ALTER TABLE futuranext.billing_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE futuranext.billing_subscriptions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE futuranext.billing_subscriptions FROM PUBLIC;
DO $$ BEGIN
    EXECUTE format(
        'CREATE POLICY owner_operations ON futuranext.billing_subscriptions TO %I USING (true) WITH CHECK (true)',
        current_user
    );
END $$;
CREATE TRIGGER audit_change AFTER INSERT OR UPDATE ON futuranext.billing_subscriptions
    FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change();

CREATE POLICY billing_admin ON futuranext.billing_subscriptions TO fn_app
    USING (tenant_id = futuranext.tenant() AND futuranext.has_permission('admin.entitlement.manage'));
CREATE POLICY billing_subscription_customer_read ON futuranext.billing_subscriptions FOR SELECT TO fn_app
    USING (tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access'));
-- O administrador da empresa só grava a assinatura durante a compra de uma ferramenta.
CREATE POLICY billing_subscription_purchase_insert ON futuranext.billing_subscriptions FOR INSERT TO fn_app
    WITH CHECK (
        tenant_id = futuranext.tenant() AND futuranext.member_role(tenant_id) = 'cliente_admin'
        AND current_setting('fn.billing_subscription_sync', true) = 'true'
    );
CREATE POLICY billing_subscription_purchase_update ON futuranext.billing_subscriptions FOR UPDATE TO fn_app
    USING (
        tenant_id = futuranext.tenant() AND futuranext.member_role(tenant_id) = 'cliente_admin'
        AND current_setting('fn.billing_subscription_sync', true) = 'true'
    );

GRANT SELECT, INSERT ON futuranext.billing_subscriptions TO fn_app;
GRANT UPDATE (environment, enabled, provider_id, provider_state, value_cents, next_due_date, updated_at)
    ON futuranext.billing_subscriptions TO fn_app;

-- Serviços ativos: o administrador da empresa lê os da própria empresa para calcular a assinatura.
CREATE POLICY billing_service_purchase_read ON futuranext.billing_services FOR SELECT TO fn_app
    USING (
        tenant_id = futuranext.tenant() AND futuranext.member_role(tenant_id) = 'cliente_admin'
        AND current_setting('fn.billing_subscription_sync', true) = 'true'
    );

-- 4. Faturas geradas pela assinatura: o webhook registra cada cobrança mensal do Asaas.
ALTER TABLE futuranext.billing_invoices ADD COLUMN subscription_id text;

CREATE FUNCTION futuranext.record_subscription_payment(
    mode text, subscription text, payment text, due date, amount integer, new_status text, url text
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    target futuranext.billing_subscriptions%ROWTYPE;
    customer text;
    invoice_id uuid;
    lines jsonb;
    lines_total bigint;
BEGIN
    IF futuranext.actor() IS NOT NULL OR futuranext.tenant() IS NOT NULL
       OR mode IS DISTINCT FROM current_setting('fn.billing_webhook', true)
       OR new_status NOT IN ('open', 'paid', 'confirmed', 'overdue', 'processing', 'refunded', 'cancelled', 'review')
       OR amount IS NULL OR amount < 1 OR due IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT * INTO target FROM futuranext.billing_subscriptions
        WHERE environment = mode AND provider_id = subscription;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT customer_id INTO customer FROM futuranext.billing_accounts
        WHERE tenant_id = target.tenant_id AND environment = mode;
    IF customer IS NULL THEN RETURN NULL; END IF;

    UPDATE futuranext.billing_invoices SET status = new_status, payment_url = url,
        due_date = due, total_cents = amount, provider_state = 'linked', updated_at = now()
    WHERE environment = mode AND provider_id = payment AND tenant_id = target.tenant_id
    RETURNING id INTO invoice_id;
    IF invoice_id IS NOT NULL THEN RETURN invoice_id; END IF;

    SELECT jsonb_agg(jsonb_build_object('name', name, 'amount_cents', amount_cents) ORDER BY name, id),
           sum(amount_cents)
      INTO lines, lines_total
      FROM futuranext.billing_services WHERE tenant_id = target.tenant_id AND active;
    IF lines IS NULL OR lines_total <> amount THEN
        lines := jsonb_build_array(jsonb_build_object(
            'name', 'Assinatura mensal FuturaNext', 'amount_cents', least(amount, 10000000)));
    END IF;
    BEGIN
        INSERT INTO futuranext.billing_invoices (
            tenant_id, period, due_date, total_cents, items, customer_id, environment, status,
            provider_state, provider_id, payment_url, subscription_id
        ) VALUES (
            target.tenant_id, date_trunc('month', due)::date, due, amount, lines, customer, mode,
            new_status, 'linked', payment, url, subscription
        ) RETURNING id INTO invoice_id;
    EXCEPTION WHEN unique_violation THEN
        -- Já existe uma fatura avulsa no mesmo mês: a cobrança fica só no Asaas para revisão manual.
        RETURN NULL;
    END;
    RETURN invoice_id;
END
$$;

-- 5. Teste grátis: um por CPF/CNPJ, criado pelo cadastro público do site.
CREATE TABLE futuranext.trials (
    tenant_id uuid PRIMARY KEY REFERENCES futuranext.organizations(id),
    document text NOT NULL UNIQUE CHECK (document ~ '^([0-9]{11}|[0-9A-Z]{12}[0-9]{2})$'),
    email text NOT NULL CHECK (email = lower(btrim(email)) AND length(email) <= 254),
    started_at timestamptz NOT NULL DEFAULT now(),
    ends_at timestamptz NOT NULL,
    CHECK (ends_at > started_at)
);
ALTER TABLE futuranext.trials ENABLE ROW LEVEL SECURITY;
ALTER TABLE futuranext.trials FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE futuranext.trials FROM PUBLIC;
DO $$ BEGIN
    EXECUTE format('CREATE POLICY owner_operations ON futuranext.trials TO %I USING (true) WITH CHECK (true)',
        current_user);
END $$;
CREATE POLICY trial_read ON futuranext.trials FOR SELECT TO fn_app USING (
    (tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access'))
    OR (futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.read'))
    OR (tenant_id = futuranext.tenant() AND futuranext.has_permission('admin.tenant.read'))
);
GRANT SELECT ON futuranext.trials TO fn_app;
CREATE TRIGGER audit_change AFTER INSERT OR UPDATE ON futuranext.trials
    FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change();

-- Chamada pela API sem usuário logado, depois de validar e limitar o cadastro público.
CREATE FUNCTION futuranext.start_trial(
    company text, company_document text, owner_email text, phone text, trial_days integer
) RETURNS TABLE (tenant_id uuid, invitation_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    org uuid;
    invite uuid;
    base text;
    address text := lower(btrim(owner_email));
BEGIN
    IF futuranext.actor() IS NOT NULL OR futuranext.tenant() IS NOT NULL
       OR current_setting('fn.public_signup', true) IS DISTINCT FROM 'true'
       OR trial_days NOT BETWEEN 1 AND 60 THEN
        RETURN;
    END IF;
    -- Um teste por CPF/CNPJ: também bloqueia documentos de empresas já cadastradas.
    PERFORM pg_advisory_xact_lock(hashtextextended('futuranext:trial:' || company_document, 0));
    IF EXISTS (SELECT FROM futuranext.trials WHERE document = company_document)
       OR EXISTS (SELECT FROM futuranext.organizations WHERE document = company_document) THEN
        RETURN;
    END IF;
    base := translate(lower(company), 'áàâãäéèêëíìîïóòôõöúùûüçñ', 'aaaaaeeeeiiiiooooouuuucn');
    base := btrim(regexp_replace(base, '[^a-z0-9]+', '-', 'g'), '-');
    base := left(coalesce(nullif(base, ''), 'empresa'), 48);
    base := btrim(base, '-');
    INSERT INTO futuranext.organizations (name, slug, document, billing_email, billing_phone)
        VALUES (btrim(company), base || '-' || left(replace(gen_random_uuid()::text, '-', ''), 8),
                company_document, address, phone)
        RETURNING id INTO org;
    INSERT INTO futuranext.trials (tenant_id, document, email, ends_at)
        VALUES (org, company_document, address, now() + make_interval(days => trial_days));
    INSERT INTO futuranext.tenant_products (tenant_id, product_id, active, starts_at, ends_at)
        SELECT org, product.id, true, now(), now() + make_interval(days => trial_days)
        FROM futuranext.products product WHERE product.active AND product.offerable;
    INSERT INTO futuranext.invitations (tenant_id, email, role, expires_at)
        VALUES (org, address, 'cliente_admin', now() + interval '7 days')
        RETURNING id INTO invite;
    tenant_id := org;
    invitation_id := invite;
    RETURN NEXT;
END
$$;

CREATE FUNCTION futuranext.record_signup_delivery(invite uuid, outcome text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    IF futuranext.actor() IS NOT NULL OR futuranext.tenant() IS NOT NULL
       OR current_setting('fn.public_signup', true) IS DISTINCT FROM 'true'
       OR outcome NOT IN ('sent', 'existing_account') THEN
        RETURN false;
    END IF;
    UPDATE futuranext.invitations SET delivery = outcome, last_send_attempt_at = now()
        WHERE id = invite AND accepted_at IS NULL;
    RETURN FOUND;
END
$$;

-- 6. Ofertas com preço e compra imediata pelo administrador da empresa.
DROP FUNCTION futuranext.tool_offers(uuid);
CREATE FUNCTION futuranext.tool_offers(org uuid)
RETURNS TABLE (id uuid, slug text, name text, description text, pending boolean, price_cents integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT product.id, product.slug, product.name, product.description, EXISTS (
        SELECT FROM futuranext.tool_requests request WHERE request.tenant_id = org
          AND request.product_id = product.id AND request.status = 'pending'
    ), product.price_cents
    FROM futuranext.products product
    WHERE org = futuranext.tenant() AND futuranext.has_permission('tenant.access')
      AND product.active AND product.offerable AND NOT EXISTS (
        SELECT FROM futuranext.tenant_products license WHERE license.tenant_id = org
          AND license.product_id = product.id AND license.active AND license.starts_at <= now()
          AND license.ends_at IS NULL
      )
    ORDER BY product.name, product.id
$$;

CREATE FUNCTION futuranext.purchase_tool(org uuid, item uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE selected futuranext.products%ROWTYPE;
BEGIN
    IF org IS DISTINCT FROM futuranext.tenant() OR NOT futuranext.tenant_active(org)
       OR futuranext.member_role(org) IS DISTINCT FROM 'cliente_admin' THEN
        RETURN false;
    END IF;
    SELECT * INTO selected FROM futuranext.products
        WHERE id = item AND active AND offerable AND price_cents IS NOT NULL;
    IF NOT FOUND THEN RETURN false; END IF;
    INSERT INTO futuranext.tenant_products (tenant_id, product_id, active, starts_at, ends_at)
        VALUES (org, item, true, now(), NULL)
    ON CONFLICT (tenant_id, product_id) DO UPDATE
        SET active = true, starts_at = least(tenant_products.starts_at, now()), ends_at = NULL;
    INSERT INTO futuranext.billing_services (tenant_id, name, amount_cents, active, product_id)
        VALUES (org, left(selected.name, 120), selected.price_cents, true, item)
    ON CONFLICT (tenant_id, product_id) WHERE product_id IS NOT NULL DO UPDATE
        SET active = true, amount_cents = excluded.amount_cents;
    UPDATE futuranext.tool_requests SET status = 'approved', decided_at = now(), decided_by = futuranext.actor()
        WHERE tenant_id = org AND product_id = item AND status = 'pending';
    RETURN true;
END
$$;

REVOKE ALL ON FUNCTION futuranext.record_subscription_payment(text, text, text, date, integer, text, text),
    futuranext.start_trial(text, text, text, text, integer), futuranext.record_signup_delivery(uuid, text),
    futuranext.tool_offers(uuid), futuranext.purchase_tool(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION futuranext.record_subscription_payment(text, text, text, date, integer, text, text),
    futuranext.start_trial(text, text, text, text, integer), futuranext.record_signup_delivery(uuid, text),
    futuranext.tool_offers(uuid), futuranext.purchase_tool(uuid, uuid) TO fn_app;
