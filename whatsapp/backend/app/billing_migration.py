"""Migração 004: cobrança isolada por empresa e ambiente."""
import hashlib
from pathlib import Path
from .core import now


async def migrate_billing(rt):
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    previous = await rt.db.schema_migrations.find_one({'_id': '004_billing'})
    if previous and previous.get('checksum') != checksum:
        raise RuntimeError('Migração 004 alterada após aplicação.')
    await rt.db.billing_accounts.create_index([('tenant_id', 1), ('environment', 1)], unique=True)
    await rt.db.billing_accounts.create_index('subscription_id', sparse=True)
    await rt.db.billing_payments.create_index([('tenant_id', 1), ('due_date', 1)])
    await rt.db.billing_events.create_index('expires_at', expireAfterSeconds=0)
    await rt.db.billing_settings.update_one({'_id': 'monthly'}, {'$setOnInsert': {'price_cents': 6000}}, upsert=True)
    await rt.db.schema_migrations.update_one({'_id': '004_billing'},
        {'$setOnInsert': {'checksum': checksum, 'applied_at': now()}}, upsert=True)
