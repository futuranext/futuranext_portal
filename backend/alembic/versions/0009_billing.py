"""Contas, serviços mensais, faturas e eventos de cobrança por empresa."""

from pathlib import Path

from alembic import op

revision = "0009_billing"
down_revision = "0008_first_password"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
        DROP TABLE futuranext.billing_events;
        DROP TABLE futuranext.billing_invoices;
        DROP TABLE futuranext.billing_services;
        DROP TABLE futuranext.billing_accounts;
        DROP TABLE futuranext.billing_customer_origins;
    """)
