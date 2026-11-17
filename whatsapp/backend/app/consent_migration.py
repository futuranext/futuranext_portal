"""Migração 002, sem alterar o checksum da migração MVP existente."""
import hashlib
from pathlib import Path
from .core import now


async def migrate_consents(rt):
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    previous = await rt.db.schema_migrations.find_one({'_id': '002_consent_requests'})
    if previous and previous.get('checksum') != checksum:
        raise RuntimeError('Migração 002 alterada após aplicação.')
    db = rt.db.consent_requests
    await db.create_index('token_hash', unique=True)
    await db.create_index([('tenant_id', 1), ('created_at', -1)])
    await db.create_index([('tenant_id', 1), ('phone_hash', 1), ('status', 1)])
    await db.create_index('purge_at', expireAfterSeconds=0)
    await rt.db.schema_migrations.update_one({'_id': '002_consent_requests'},
        {'$setOnInsert': {'checksum': checksum, 'applied_at': now()}}, upsert=True)
