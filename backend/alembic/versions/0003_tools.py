"""Catálogo de ferramentas, acessos e solicitações."""

from pathlib import Path

from alembic import op

revision = "0003_tools"
down_revision = "0002_invitation_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # Os cadastros iniciais podem ter sido usados por clientes desde a migração.
    op.execute("""
        DROP FUNCTION futuranext.review_tool_request(uuid, text);
        DROP FUNCTION futuranext.request_tool(uuid, uuid);
        DROP FUNCTION futuranext.tool_offers(uuid);
        DROP FUNCTION futuranext.set_account_access(uuid, uuid, text, boolean);
        DROP FUNCTION futuranext.account_access(uuid);
        DROP FUNCTION futuranext.search_accounts(text);
        DROP FUNCTION futuranext.can_use_tool(uuid, text, boolean);
        DROP TABLE futuranext.tool_requests;
        ALTER TABLE futuranext.products DROP COLUMN offerable, DROP COLUMN launch_url;
    """)
