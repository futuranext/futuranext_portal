"""Agenda, disponibilidade e configuração de conversa por empresa e produto."""

from pathlib import Path

from alembic import op

revision = "0005_agenda"
down_revision = "0004_fiscal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).with_name("0005_agenda.sql")
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
        DROP TABLE futuranext.agenda_slots, futuranext.agenda_hours,
            futuranext.agenda_services, futuranext.agenda_professionals,
            futuranext.agenda_conversation
    """)
