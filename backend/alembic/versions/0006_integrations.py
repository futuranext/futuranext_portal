"""Preferências do webhook e metadados de eventos do WhatsApp."""

from pathlib import Path

from alembic import op

revision = "0006_integrations"
down_revision = "0005_agenda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).with_name("0006_integrations.sql")
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION futuranext.register_whatsapp_event(uuid, uuid, uuid, text, timestamptz)"
    )
    op.execute("DROP TABLE futuranext.whatsapp_events")
    op.execute("DROP TABLE futuranext.whatsapp_webhooks")
