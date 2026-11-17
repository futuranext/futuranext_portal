"""Produtos oferecidos pela barbearia e pela clínica."""

from alembic import op

revision = "0007_agenda_products"
down_revision = "0006_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE futuranext.agenda_offerings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES futuranext.organizations(id),
            tool_slug text NOT NULL CHECK (tool_slug IN ('barbearia', 'clinica')),
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
            description text NOT NULL DEFAULT '' CHECK (length(description) <= 600),
            price_cents integer NOT NULL CHECK (price_cents BETWEEN 0 AND 99999999),
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX agenda_offerings_scope ON futuranext.agenda_offerings (tenant_id, tool_slug);
        ALTER TABLE futuranext.agenda_offerings ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON futuranext.agenda_offerings FROM PUBLIC;
        GRANT SELECT, INSERT ON futuranext.agenda_offerings TO fn_app;
        GRANT UPDATE (name, description, price_cents, active) ON futuranext.agenda_offerings TO fn_app;

        CREATE POLICY agenda_read ON futuranext.agenda_offerings FOR SELECT TO fn_app
            USING (futuranext.can_use_tool(tenant_id, tool_slug, false));
        CREATE POLICY agenda_create ON futuranext.agenda_offerings FOR INSERT TO fn_app
            WITH CHECK (futuranext.can_use_tool(tenant_id, tool_slug, true));
        CREATE POLICY agenda_update ON futuranext.agenda_offerings FOR UPDATE TO fn_app
            USING (futuranext.can_use_tool(tenant_id, tool_slug, true))
            WITH CHECK (futuranext.can_use_tool(tenant_id, tool_slug, true));
        CREATE TRIGGER audit_change AFTER INSERT OR UPDATE ON futuranext.agenda_offerings
            FOR EACH ROW EXECUTE FUNCTION futuranext.audit_change();
    """)


def downgrade() -> None:
    op.execute("DROP TABLE futuranext.agenda_offerings")
