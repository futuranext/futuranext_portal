"""Configuração fiscal e certificados A1 isolados por empresa e ferramenta."""

from alembic import op

revision = "0004_fiscal"
down_revision = "0003_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE futuranext.fiscal_tool_settings (
            tenant_id uuid REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
            tool_slug text CHECK (tool_slug IN ('nfse', 'nfse-consulta')),
            settings jsonb NOT NULL CHECK (jsonb_typeof(settings) = 'object'),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, tool_slug)
        );

        CREATE TABLE futuranext.fiscal_certificates (
            tenant_id uuid REFERENCES futuranext.organizations(id) ON DELETE CASCADE,
            tool_slug text CHECK (tool_slug IN ('nfse', 'nfse-consulta')),
            not_before timestamptz NOT NULL,
            not_after timestamptz NOT NULL CHECK (not_after > not_before),
            sealed_payload bytea NOT NULL CHECK (octet_length(sealed_payload) BETWEEN 32 AND 132200),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, tool_slug)
        );

        ALTER TABLE futuranext.fiscal_tool_settings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE futuranext.fiscal_tool_settings FORCE ROW LEVEL SECURITY;
        ALTER TABLE futuranext.fiscal_certificates ENABLE ROW LEVEL SECURITY;
        ALTER TABLE futuranext.fiscal_certificates FORCE ROW LEVEL SECURITY;

        DO $$
        DECLARE relation text;
        BEGIN
            FOREACH relation IN ARRAY ARRAY['fiscal_tool_settings', 'fiscal_certificates'] LOOP
                EXECUTE format(
                    'CREATE POLICY owner_operations ON futuranext.%I TO %I
                     USING (true) WITH CHECK (true)', relation, current_user
                );
                EXECUTE format(
                    'CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON futuranext.%I
                     FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change()', relation
                );
            END LOOP;
        END
        $$;

        CREATE POLICY fiscal_settings_read ON futuranext.fiscal_tool_settings FOR SELECT TO fn_app
            USING (futuranext.can_use_tool(tenant_id, tool_slug, false));
        CREATE POLICY fiscal_settings_insert ON futuranext.fiscal_tool_settings FOR INSERT TO fn_app
            WITH CHECK (futuranext.can_use_tool(tenant_id, tool_slug, true));
        CREATE POLICY fiscal_settings_update ON futuranext.fiscal_tool_settings FOR UPDATE TO fn_app
            USING (futuranext.can_use_tool(tenant_id, tool_slug, true))
            WITH CHECK (futuranext.can_use_tool(tenant_id, tool_slug, true));

        CREATE POLICY fiscal_certificates_read ON futuranext.fiscal_certificates FOR SELECT TO fn_app
            USING (futuranext.can_use_tool(tenant_id, tool_slug, false));
        CREATE POLICY fiscal_certificates_insert ON futuranext.fiscal_certificates FOR INSERT TO fn_app
            WITH CHECK (futuranext.has_mfa() AND futuranext.can_use_tool(tenant_id, tool_slug, true));
        CREATE POLICY fiscal_certificates_update ON futuranext.fiscal_certificates FOR UPDATE TO fn_app
            USING (futuranext.has_mfa() AND futuranext.can_use_tool(tenant_id, tool_slug, true))
            WITH CHECK (futuranext.has_mfa() AND futuranext.can_use_tool(tenant_id, tool_slug, true));
        CREATE POLICY fiscal_certificates_delete ON futuranext.fiscal_certificates FOR DELETE TO fn_app
            USING (futuranext.has_mfa() AND futuranext.can_use_tool(tenant_id, tool_slug, true));

        REVOKE ALL ON futuranext.fiscal_tool_settings, futuranext.fiscal_certificates FROM PUBLIC;
        GRANT SELECT, INSERT ON futuranext.fiscal_tool_settings TO fn_app;
        GRANT UPDATE (settings, updated_at) ON futuranext.fiscal_tool_settings TO fn_app;
        GRANT SELECT (tenant_id, tool_slug, not_before, not_after, updated_at)
            ON futuranext.fiscal_certificates TO fn_app;
        GRANT INSERT, DELETE ON futuranext.fiscal_certificates TO fn_app;
        GRANT UPDATE (not_before, not_after, sealed_payload, updated_at)
            ON futuranext.fiscal_certificates TO fn_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE futuranext.fiscal_certificates, futuranext.fiscal_tool_settings")
