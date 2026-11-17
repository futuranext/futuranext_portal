"""Migração idempotente de índices; lock Mongo impede execuções concorrentes."""
import asyncio
import hashlib
from pathlib import Path
from datetime import timedelta
from pymongo.errors import DuplicateKeyError
from .core import Runtime, Settings, now, new_id


async def migrate(rt: Runtime):
    owner = new_id()
    acquired = False
    for _ in range(60):
        try:
            result = await rt.db.schema_migrations.update_one(
                {'_id': 'lock', 'expires_at': {'$lt': now()}},
                {'$set': {'owner': owner, 'expires_at': now() + timedelta(minutes=2)}}, upsert=True)
            acquired = bool(result.modified_count or result.upserted_id)
        except DuplicateKeyError:
            pass
        if acquired:
            break
        await asyncio.sleep(1)
    if not acquired:
        raise RuntimeError('Migração já está em execução.')
    try:
        db = rt.db
        checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        previous = await db.schema_migrations.find_one({'_id': '001_mvp'})
        if previous and previous.get('checksum') and previous['checksum'] != checksum:
            raise RuntimeError('Migração 001 alterada após aplicação; crie uma nova versão.')
        await db.users.create_index('email', unique=True)
        await db.instances.create_index([('tenant_id', 1), ('name', 1)], unique=True)
        await db.instances.create_index('provider_ref', unique=True)
        await db.sessions.create_index('expires_at', expireAfterSeconds=0)
        await db.api_keys.create_index('secret_hash', unique=True)
        await db.messages.create_index([('tenant_id', 1), ('instance_id', 1), ('idempotency_key', 1)],
            unique=True, partialFilterExpression={'direction': 'outbound', 'idempotency_key': {'$type': 'string'}})
        await db.messages.create_index([('tenant_id', 1), ('instance_id', 1), ('provider_message_id', 1), ('direction', 1)],
            unique=True, partialFilterExpression={'direction': 'inbound', 'provider_message_id': {'$type': 'string'}})
        await db.messages.create_index([('status', 1), ('due_at', 1)])
        await db.messages.create_index([('tenant_id', 1), ('created_at', -1), ('_id', -1)])
        for name in ('messages', 'gateway_events', 'audit_events'):
            await db[name].create_index('expires_at', expireAfterSeconds=0)
        await db.gateway_events.create_index([('processed', 1), ('created_at', 1)])
        await db.consents.create_index([('tenant_id', 1), ('phone_hash', 1)], unique=True)
        await db.suppressions.create_index([('tenant_id', 1), ('phone_hash', 1)], unique=True)
        await db.schema_migrations.update_one({'_id': '001_mvp'}, {'$set': {'applied_at': now(),
            'checksum': checksum, 'description': 'Índices MVP v1; alterações futuras usam nova versão'}}, upsert=True)
    finally:
        await rt.db.schema_migrations.delete_one({'_id': 'lock', 'owner': owner})


async def main():
    rt = Runtime(Settings())
    try:
        await migrate(rt)
        print('Migração concluída.')
    finally:
        await rt.close()


if __name__ == '__main__':
    asyncio.run(main())
