"""Dados de cobrança da empresa e vínculo automático do cliente Asaas."""

from pathlib import Path

from alembic import op

revision = "0011_billing_customer"
down_revision = "0010_user_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
        DROP POLICY billing_account_sync_insert ON futuranext.billing_accounts;
        DROP POLICY billing_account_customer_read ON futuranext.billing_accounts;
        DROP POLICY billing_customer_origin_sync_insert ON futuranext.billing_customer_origins;
        DROP POLICY billing_customer_origin_sync_read ON futuranext.billing_customer_origins;
        REVOKE UPDATE (document, billing_email, billing_phone) ON futuranext.organizations FROM fn_app;
        ALTER TABLE futuranext.organizations
            DROP COLUMN billing_phone, DROP COLUMN billing_email, DROP COLUMN document;
    """)
