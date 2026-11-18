"""Persistência do serviço WhatsApp no PostgreSQL do portal (schema privado whatsapp)."""

from pathlib import Path

from alembic import op

revision = "0013_whatsapp_service"
down_revision = "0012_whatsapp_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # Remove somente os objetos criados nesta revisão, um a um (sem CASCADE).
    # Os papéis wa_api e wa_worker são preservados: desative-os com ALTER ROLE ... NOLOGIN.
    op.execute("""
        DROP FUNCTION whatsapp.terms_accepted(uuid, uuid);
        DROP FUNCTION whatsapp.webhook_instance(uuid);
        DROP FUNCTION whatsapp.api_key_identity(text);
        DROP TABLE whatsapp.webhook_deliveries;
        DROP TABLE whatsapp.callback_destinations;
        DROP TABLE whatsapp.gateway_events;
        DROP TABLE whatsapp.audit_events;
        DROP TABLE whatsapp.suppressions;
        DROP TABLE whatsapp.messages;
        DROP TABLE whatsapp.api_key_instances;
        DROP TABLE whatsapp.api_keys;
        DROP TABLE whatsapp.instances;
        DROP FUNCTION whatsapp.tenant_licensed(uuid);
        DROP FUNCTION whatsapp.tenant();
        REVOKE ALL ON SCHEMA whatsapp FROM wa_api, wa_worker;
        DROP SCHEMA whatsapp;
    """)
