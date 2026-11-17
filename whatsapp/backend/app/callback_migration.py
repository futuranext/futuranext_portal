"""Migração 005 para a fila de callbacks de saída."""
import hashlib
from pathlib import Path

from .core import now


async def migrate_callbacks(rt):
    migration = {'_id': '005_outbound_webhooks'}
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    previous = await rt.db.schema_migrations.find_one(migration)
    if previous and previous.get('checksum') != checksum:
        raise RuntimeError('Migração 005 alterada após aplicação.')
    await rt.db.webhook_deliveries.create_index([('status', 1), ('due_at', 1)])
    await rt.db.webhook_deliveries.create_index([('status', 1), ('lease_until', 1)])
    await rt.db.webhook_deliveries.create_index('expires_at', expireAfterSeconds=0)
    await rt.db.schema_migrations.update_one(
        migration, {'$setOnInsert': {'checksum': checksum, 'applied_at': now()}}, upsert=True)
