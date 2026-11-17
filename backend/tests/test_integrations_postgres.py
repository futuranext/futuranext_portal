import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text
from test_postgres import Platform, platform  # noqa: F401 - fixture PostgreSQL compartilhada

from app.modules.external_whatsapp import WhatsAppEvent
from app.modules.integrations import persist_event
from app.repository import Repository


def test_event_receipt_is_deduplicated_and_respects_license(
    platform: Platform,  # noqa: F811
    monkeypatch,
) -> None:
    instance = uuid4()
    event_id = uuid4()
    secret = "a" * 44
    monkeypatch.setenv(
        "FN_EXTERNAL_INTEGRATIONS",
        json.dumps(
            {
                str(platform.tenant_a): {
                    "whatsapp_instance_id": str(instance),
                    "whatsapp_webhook_secret": secret,
                },
            }
        ),
    )
    # O runtime usa o papel fn_app sem bypass de RLS.
    with platform.owner.begin() as db:
        product_id = db.scalar(text("SELECT id FROM futuranext.products WHERE slug = 'whatsapp'"))
        assert product_id is not None

    event = WhatsAppEvent(
        id=event_id,
        type="message.received",
        instance_id=instance,
        occurred_at=datetime.now(UTC),
    )
    assert persist_event(platform.database, platform.tenant_a, event) == "unavailable"
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as db:
        Repository(db).upsert_entitlement(platform.tenant_a, product_id, True, None, None)
    # O painel antigo de metadados saiu do portal; o recebimento continua para destinos legados.
    with platform.owner.begin() as db:
        db.execute(
            text("INSERT INTO futuranext.whatsapp_webhooks (tenant_id, instance_id) VALUES (:t, :i)"),
            {"t": platform.tenant_a, "i": instance},
        )

    assert persist_event(platform.database, platform.tenant_a, event) == "accepted"
    assert persist_event(platform.database, platform.tenant_a, event) == "duplicate"
    assert (
        persist_event(
            platform.database,
            platform.tenant_a,
            event.model_copy(
                update={"type": "message.status"},
            ),
        )
        == "conflict"
    )
    assert persist_event(platform.database, platform.tenant_b, event) == "unavailable"

    query = text("SELECT tenant_id, event_id FROM futuranext.whatsapp_events")
    with platform.database.session(platform.alice, "aal2", platform.tenant_a) as db:
        assert list(db.execute(query)) == [(platform.tenant_a, event.id)]
    with platform.database.session(platform.bob, "aal2", platform.tenant_b) as db:
        assert list(db.execute(query)) == []
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as db:
        Repository(db).upsert_entitlement(platform.tenant_a, product_id, False, None, None)
    assert persist_event(platform.database, platform.tenant_a, event) == "unavailable"
