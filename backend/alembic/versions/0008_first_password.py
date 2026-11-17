"""Provisionamento de senha temporária e controle de sessões."""

from pathlib import Path

from alembic import op

revision = "0008_first_password"
down_revision = "0007_agenda_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION futuranext.link_temporary_account(uuid, uuid, text);
        DROP FUNCTION futuranext.find_temporary_account(uuid, text);
        DROP FUNCTION futuranext.password_session_current(text);
    """)
