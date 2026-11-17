-- Dados exigidos pelo Asaas para criar o cliente de cobrança da empresa.
-- As colunas aceitam nulo para preservar empresas anteriores; a API exige os três campos.
ALTER TABLE futuranext.organizations
    ADD COLUMN document text
        CONSTRAINT organizations_document_check
        CHECK (document ~ '^([0-9]{11}|[0-9A-Z]{12}[0-9]{2})$'),
    ADD COLUMN billing_email text
        CONSTRAINT organizations_billing_email_check
        CHECK (billing_email = lower(btrim(billing_email)) AND length(billing_email) BETWEEN 3 AND 254),
    ADD COLUMN billing_phone text
        CONSTRAINT organizations_billing_phone_check
        CHECK (billing_phone ~ '^[1-9][0-9]{9,10}$');

GRANT UPDATE (document, billing_email, billing_phone) ON futuranext.organizations TO fn_app;

-- Um usuário da própria empresa pode registrar o primeiro vínculo Asaas criado automaticamente.
-- A aplicação só ativa a flag depois de criar ou localizar o cliente pela referência da empresa.
CREATE POLICY billing_customer_origin_sync_read ON futuranext.billing_customer_origins FOR SELECT TO fn_app
    USING (
        tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access')
        AND current_setting('fn.billing_customer_sync', true) = 'true'
    );

CREATE POLICY billing_customer_origin_sync_insert ON futuranext.billing_customer_origins FOR INSERT TO fn_app
    WITH CHECK (
        tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access')
        AND current_setting('fn.billing_customer_sync', true) = 'true'
    );

CREATE POLICY billing_account_customer_read ON futuranext.billing_accounts FOR SELECT TO fn_app
    USING (tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access'));

CREATE POLICY billing_account_sync_insert ON futuranext.billing_accounts FOR INSERT TO fn_app
    WITH CHECK (
        tenant_id = futuranext.tenant() AND futuranext.has_permission('tenant.access')
        AND current_setting('fn.billing_customer_sync', true) = 'true'
    );
