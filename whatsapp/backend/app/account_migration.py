"""Migração 003: cadastro verificado, sem modificar migrações anteriores."""
import hashlib
from pathlib import Path
from .core import now


async def migrate_accounts(rt):
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    previous = await rt.db.schema_migrations.find_one({'_id': '003_accounts'})
    if previous and previous.get('checksum') != checksum:
        raise RuntimeError('Migração 003 alterada após aplicação.')
    await rt.db.account_requests.create_index('token_hash', unique=True)
    await rt.db.account_requests.create_index('expires_at', expireAfterSeconds=0)
    await rt.db.account_requests.create_index('email_hash')
    await rt.db.schema_migrations.update_one({'_id': '003_accounts'},
        {'$setOnInsert': {'checksum': checksum, 'applied_at': now()}}, upsert=True)
