-- Aceite dos termos de uso do WhatsApp pelo administrador que pareia o número.
-- O link enviado por e-mail só tem efeito na sessão do mesmo usuário, na mesma empresa.
CREATE TABLE futuranext.whatsapp_consents (
    tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES futuranext.profiles(user_id) ON DELETE CASCADE,
    email text NOT NULL CHECK (length(email) BETWEEN 3 AND 254),
    terms_version text NOT NULL CHECK (terms_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    token_hash text CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    requested_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    accepted_at timestamptz,
    PRIMARY KEY (tenant_id, user_id),
    CHECK ((accepted_at IS NULL) = (token_hash IS NOT NULL))
);

ALTER TABLE futuranext.whatsapp_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE futuranext.whatsapp_consents FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    EXECUTE format(
        'CREATE POLICY owner_operations ON futuranext.whatsapp_consents TO %I USING (true) WITH CHECK (true)',
        current_user
    );
END
$$;

CREATE POLICY whatsapp_consent_own_read ON futuranext.whatsapp_consents FOR SELECT TO fn_app
    USING (user_id = futuranext.actor() AND futuranext.can_use_tool(tenant_id, 'whatsapp', true));

CREATE POLICY whatsapp_consent_own_insert ON futuranext.whatsapp_consents FOR INSERT TO fn_app
    WITH CHECK (user_id = futuranext.actor() AND futuranext.can_use_tool(tenant_id, 'whatsapp', true));

CREATE POLICY whatsapp_consent_own_update ON futuranext.whatsapp_consents FOR UPDATE TO fn_app
    USING (user_id = futuranext.actor() AND futuranext.can_use_tool(tenant_id, 'whatsapp', true))
    WITH CHECK (user_id = futuranext.actor() AND futuranext.can_use_tool(tenant_id, 'whatsapp', true));

CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON futuranext.whatsapp_consents
    FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change();

REVOKE ALL ON TABLE futuranext.whatsapp_consents FROM PUBLIC;
GRANT SELECT, INSERT ON TABLE futuranext.whatsapp_consents TO fn_app;
GRANT UPDATE (email, terms_version, token_hash, requested_at, expires_at, accepted_at)
    ON TABLE futuranext.whatsapp_consents TO fn_app;
