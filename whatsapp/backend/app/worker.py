"""Worker: outbox PostgreSQL durável e Redis Streams apenas como sinalização.

Filas usam `FOR UPDATE SKIP LOCKED` em transações curtas e uma lease por item; a chamada ao
gateway acontece fora da transação. Vários workers podem rodar ao mesmo tempo.
"""
import asyncio
import json
import random
import time
from datetime import timedelta

from fastapi import HTTPException

from .api import recipient_allowed
from .callbacks import (
    DestinationRegistry,
    callback_loop,
    enqueue_callbacks,
    load_destinations,
)
from .core import Runtime, Settings, fetch_one, new_id, now
from .events import process_event
from .providers import ProviderError

LEASE_SECONDS = 90
RETENTION_BATCH = 1000


async def recover_messages(rt):
    async with rt.db.tx() as conn:
        # Envio iniciado sem confirmação: o resultado é incerto e não pode ser repetido.
        await conn.execute('''
            UPDATE whatsapp.messages SET status = 'unknown', error_code = 'WORKER_INTERRUPTED', updated_at = now()
            WHERE status = 'processing' AND lease_until < now() AND dispatch_started_at IS NOT NULL''')
        await conn.execute('''
            UPDATE whatsapp.messages SET status = 'queued', due_at = now()
            WHERE status = 'processing' AND lease_until < now() AND dispatch_started_at IS NULL''')


async def claim_message(rt, owner):
    async with rt.db.tx() as conn:
        return await fetch_one(conn, '''
            UPDATE whatsapp.messages SET status = 'processing', lease_owner = %s,
                lease_until = now() + make_interval(secs => %s)
            WHERE id = (
                SELECT id FROM whatsapp.messages
                WHERE status IN ('queued', 'retry_wait') AND due_at <= now() AND expires_at > now()
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *''', (owner, LEASE_SECONDS))


async def dispatch_one(rt):
    await recover_messages(rt)
    owner = new_id()
    doc = await claim_message(rt, owner)
    if not doc:
        return
    tenant = doc['tenant_id']
    guard = ('WHERE id = %s AND tenant_id = %s AND lease_owner = %s AND status = %s')
    scope = (doc['id'], tenant, owner, 'processing')

    async def finish(status, code=None, *, due_at=None, provider_message_id=None, restart=False):
        async with rt.db.tx() as conn:
            await conn.execute(
                'UPDATE whatsapp.messages SET status = %s, error_code = %s, updated_at = now(), '
                'due_at = coalesce(%s, due_at), provider_message_id = coalesce(%s, provider_message_id), '
                'dispatch_started_at = CASE WHEN %s THEN NULL ELSE dispatch_started_at END ' + guard,
                (status, code, due_at, provider_message_id, restart, *scope))

    async with rt.db.tx() as conn:
        state = await fetch_one(conn, '''
            SELECT whatsapp.tenant_licensed(%s) AS licensed,
                   (SELECT connection_state FROM whatsapp.instances WHERE id = %s AND tenant_id = %s) AS connection,
                   (SELECT provider_ref FROM whatsapp.instances WHERE id = %s AND tenant_id = %s) AS provider_ref''',
            (tenant, doc['instance_id'], tenant, doc['instance_id'], tenant))
        allowed = await recipient_allowed(conn, tenant, doc['phone_hash'])
    if doc['content_expires_at'] <= now() or not doc['content']:
        await finish('cancelled', 'CONTENT_EXPIRED')
        return
    if not allowed:
        await finish('cancelled', 'OPTED_OUT')
        return
    if not state['licensed']:
        await finish('cancelled', 'LICENSE_INACTIVE')
        return
    if state['connection'] in (None, 'deleting', 'error'):
        await finish('cancelled', 'INSTANCE_UNAVAILABLE')
        return
    if state['connection'] != 'connected':
        await finish('retry_wait', 'NOT_CONNECTED', due_at=now() + timedelta(seconds=30))
        return
    try:
        await rt.limit(f'send-instance:{doc["instance_id"]}', 1, 5)
        await rt.limit(f'send-day:{tenant}:{now().date()}', 100, 86400)
    except HTTPException as exc:
        await finish('retry_wait', 'OPERATIONAL_LIMIT', due_at=now() + timedelta(seconds=60 if exc.status_code == 429 else 10))
        return
    async with rt.db.tx() as conn:
        # Nova consulta logo antes do envio: um opt-out pode ter chegado durante as verificações.
        if not await recipient_allowed(conn, tenant, doc['phone_hash']):
            allowed = False
        else:
            started = await conn.execute(
                'UPDATE whatsapp.messages SET dispatch_started_at = now(), attempts = attempts + 1 ' + guard, scope)
    if not allowed:
        await finish('cancelled', 'OPTED_OUT')
        return
    if not started.rowcount:
        return
    content = rt.unseal(doc['content'])
    try:
        sent = await rt.provider.send_text(state['provider_ref'], content['phone'], content['message'])
        provider_id = (sent.get('key') or {}).get('id')
        if not provider_id:
            await finish('unknown', 'NO_PROVIDER_ID')
        else:
            await finish('sent', provider_message_id=str(provider_id)[:200])
    except ProviderError as exc:
        attempts = doc['attempts'] + 1
        if exc.uncertain:
            await finish('unknown', exc.code)
        elif exc.retryable and attempts < 3:
            await finish('retry_wait', exc.code, restart=True,
                         due_at=now() + timedelta(seconds=(5, 30, 120)[attempts - 1] + random.uniform(0, 3)))
        else:
            await finish('failed', exc.code)


async def process_events(rt, registry, limit=50):
    """Processa eventos um a um, cada um na própria transação com o evento bloqueado."""
    for _ in range(limit):
        async with rt.db.tx() as conn:
            event = await fetch_one(conn, '''
                SELECT * FROM whatsapp.gateway_events
                WHERE NOT processed AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED''')
            if not event:
                return
            if await process_event(rt, conn, event):
                if destinations := await registry.current():
                    await enqueue_callbacks(conn, event, destinations)
                await conn.execute('UPDATE whatsapp.gateway_events SET processed = true WHERE event_key = %s',
                                   (event['event_key'],))
            else:
                expired = event['created_at'] < now() - timedelta(hours=1)
                await conn.execute('''
                    UPDATE whatsapp.gateway_events SET processed = %s, outcome = %s,
                        next_attempt_at = now() + interval '30 seconds' WHERE event_key = %s''',
                    (expired, 'unmatched' if expired else 'awaiting_correlation', event['event_key']))


async def refresh_instances(rt):
    async with rt.db.tx() as conn:
        rows = await (await conn.execute('''
            SELECT id, tenant_id, provider_ref, connection_state FROM whatsapp.instances
            WHERE connection_state NOT IN ('creating', 'error', 'deleting') ORDER BY created_at LIMIT 100''')).fetchall()
    for instance in rows:
        try:
            state = await rt.provider.connection(instance['provider_ref'])
            changes = {'state': state, 'connected': None, 'disconnected': None}
            if state != instance['connection_state'] and state in ('connected', 'disconnected'):
                changes['connected' if state == 'connected' else 'disconnected'] = now()
            async with rt.db.tx() as conn:
                await conn.execute('''
                    UPDATE whatsapp.instances SET connection_state = %s, observed_at = now(), last_error = NULL,
                        last_connected_at = coalesce(%s, last_connected_at),
                        last_disconnected_at = coalesce(%s, last_disconnected_at)
                    WHERE id = %s AND tenant_id = %s AND connection_state <> 'deleting' ''',
                    (state, changes['connected'], changes['disconnected'], instance['id'], instance['tenant_id']))
        except ProviderError as exc:
            async with rt.db.tx() as conn:
                await conn.execute('UPDATE whatsapp.instances SET last_error = %s WHERE id = %s AND tenant_id = %s',
                                   (exc.code, instance['id'], instance['tenant_id']))


async def apply_retention(rt):
    """Equivalente aos índices TTL usados antes no MongoDB, em lotes para não segurar locks."""
    async with rt.db.tx() as conn:
        await conn.execute('''
            UPDATE whatsapp.messages SET content = NULL WHERE id IN (
                SELECT id FROM whatsapp.messages WHERE content IS NOT NULL AND content_expires_at <= now() LIMIT %s)''',
            (RETENTION_BATCH,))
        for table in ('messages', 'gateway_events', 'webhook_deliveries', 'audit_events'):
            key = 'event_key' if table == 'gateway_events' else 'id'
            await conn.execute(
                f'DELETE FROM whatsapp.{table} WHERE {key} IN ('
                f'SELECT {key} FROM whatsapp.{table} WHERE expires_at <= now() LIMIT %s)', (RETENTION_BATCH,))


async def maintenance(rt):
    await refresh_instances(rt)
    await apply_retention(rt)


async def main():
    config = Settings()
    try:
        static_destinations = load_destinations(config.whatsapp_outbound_webhooks.get_secret_value())
    except ValueError:
        print('{"level":"error","event":"callback_config_invalid"}', flush=True)
        static_destinations = {}
    rt = Runtime(config)
    await rt.open()
    registry = DestinationRegistry(rt, static_destinations)
    background_tasks = [asyncio.create_task(callback_loop(rt, registry))]
    last_check, cursor = 0.0, '0-0'
    try:
        while True:
            try:
                await rt.redis.set('worker:heartbeat', now().isoformat(), ex=90)
                await process_events(rt, registry)
                await dispatch_one(rt)
                if time.monotonic() - last_check > 30:
                    await maintenance(rt)
                    last_check = time.monotonic()
                signals = await rt.redis.xread({'dispatch': cursor}, count=20, block=1000)
                if signals:
                    cursor = signals[-1][1][-1][0]
            except Exception as exc:
                print(json.dumps({'level': 'error', 'event': 'worker_iteration_failed', 'error_type': type(exc).__name__}), flush=True)
                await asyncio.sleep(3)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await rt.close()


if __name__ == '__main__':
    asyncio.run(main())
