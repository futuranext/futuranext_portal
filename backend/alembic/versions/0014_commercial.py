"""Preço das ferramentas, compra pelo portal, assinatura mensal no Asaas e teste grátis."""

from pathlib import Path

from alembic import op

revision = "0014_commercial"
down_revision = "0013_whatsapp_service"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # Remove somente os objetos desta revisão (sem CASCADE) e restaura a função de ofertas anterior.
    op.execute("""
        DROP FUNCTION futuranext.purchase_tool(uuid, uuid);
        DROP FUNCTION futuranext.tool_offers(uuid);
        CREATE FUNCTION futuranext.tool_offers(org uuid)
        RETURNS TABLE (id uuid, slug text, name text, description text, pending boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            SELECT product.id, product.slug, product.name, product.description, EXISTS (
                SELECT FROM futuranext.tool_requests request WHERE request.tenant_id = org
                  AND request.product_id = product.id AND request.status = 'pending'
            ) FROM futuranext.products product
            WHERE org = futuranext.tenant() AND futuranext.has_permission('tenant.access')
              AND product.active AND product.offerable AND NOT EXISTS (
                SELECT FROM futuranext.tenant_products license WHERE license.tenant_id = org
                  AND license.product_id = product.id AND license.active AND license.starts_at <= now()
                  AND (license.ends_at IS NULL OR license.ends_at > now())
              )
            ORDER BY product.name, product.id
        $$;
        REVOKE ALL ON FUNCTION futuranext.tool_offers(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION futuranext.tool_offers(uuid) TO fn_app;

        DROP FUNCTION futuranext.record_signup_delivery(uuid, text);
        DROP FUNCTION futuranext.start_trial(text, text, text, text, integer);
        DROP TABLE futuranext.trials;

        DROP FUNCTION futuranext.record_subscription_payment(text, text, text, date, integer, text, text);
        ALTER TABLE futuranext.billing_invoices DROP COLUMN subscription_id;

        DROP POLICY billing_service_purchase_read ON futuranext.billing_services;
        DROP TABLE futuranext.billing_subscriptions;

        DROP INDEX futuranext.billing_service_per_product;
        ALTER TABLE futuranext.billing_services DROP COLUMN product_id;

        REVOKE UPDATE (price_cents) ON futuranext.products FROM fn_app;
        ALTER TABLE futuranext.products DROP COLUMN price_cents;
    """)
