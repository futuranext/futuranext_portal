"""Worker do MVP: outbox Mongo durável e Redis Streams como sinalização."""
import asyncio
import json
import random
import time
from datetime import timedelta
from fastapi import HTTPException
from pymongo import ReturnDocument
from .api import recipient_allowed
from .callback_migration import migrate_callbacks
from .callbacks import DestinationRegistry, callback_loop, enqueue_callbacks, load_destinations
from .core import Runtime, Settings, now, new_id
from .events import process_event
from .migrations import migrate
from .providers import ProviderError


async def dispatch_one(rt):
    timestamp = now()
    await rt.db.messages.update_many({'status': 'processing', 'lease_until': {'$lt': timestamp},
        'dispatch_started_at': {'$exists': True}}, {'$set': {'status': 'unknown', 'error_code': 'WORKER_INTERRUPTED'}})
    await rt.db.messages.update_many({'status': 'processing', 'lease_until': {'$lt': timestamp},
        'dispatch_started_at': {'$exists': False}}, {'$set': {'status': 'queued', 'due_at': timestamp}})
    owner = new_id()
    doc = await rt.db.messages.find_one_and_update(
        {'status': {'$in': ['queued', 'retry_wait']}, 'due_at': {'$lte': timestamp}, 'expires_at': {'$gt': timestamp}},
        {'$set': {'status': 'processing', 'lease_owner': owner, 'lease_until': timestamp + timedelta(seconds=90)}},
        sort=[('created_at', 1)], return_document=ReturnDocument.AFTER)
    if not doc:
        return
    query = {'_id': doc['_id'], 'tenant_id': doc['tenant_id'], 'lease_owner': owner, 'status': 'processing'}

    async def finish(status, code=None, **extra):
        await rt.db.messages.update_one(query, {'$set': {'status': status, 'error_code': code, 'updated_at': now(), **extra}})

    if doc['content_expires_at'] <= now():
        await finish('cancelled', 'CONTENT_EXPIRED')
        return
    if not await recipient_allowed(rt, doc['tenant_id'], doc['phone_hash']):
        await finish('cancelled', 'OPTED_OUT')
        return
    instance = await rt.db.instances.find_one({'_id': doc['instance_id'], 'tenant_id': doc['tenant_id']})
    if not instance or instance['connection_state'] in ('deleting', 'error'):
        await finish('cancelled', 'INSTANCE_UNAVAILABLE')
        return
    if instance['connection_state'] != 'connected':
        await finish('retry_wait', 'NOT_CONNECTED', due_at=now() + timedelta(seconds=30))
        return
    try:
        await rt.limit(f'send-instance:{instance["_id"]}', 1, 5)
        await rt.limit(f'send-day:{doc["tenant_id"]}:{now().date()}', 100, 86400)
    except HTTPException as exc:
        await finish('retry_wait', 'OPERATIONAL_LIMIT', due_at=now() + timedelta(seconds=60 if exc.status_code == 429 else 10))
        return
    if not await recipient_allowed(rt, doc['tenant_id'], doc['phone_hash']):
        await finish('cancelled', 'OPTED_OUT')
        return
    content = rt.unseal(doc['content'])
    result = await rt.db.messages.update_one(query, {'$set': {'dispatch_started_at': now()}, '$inc': {'attempts': 1}})
    if not result.matched_count:
        return
    try:
        sent = await rt.provider.send_text(instance['provider_ref'], content['phone'], content['message'])
        provider_id = (sent.get('key') or {}).get('id')
        if not provider_id:
            await finish('unknown', 'NO_PROVIDER_ID')
        else:
            await finish('sent', provider_message_id=str(provider_id))
    except ProviderError as exc:
        attempts = doc.get('attempts', 0) + 1
        if exc.uncertain:
            await finish('unknown', exc.code)
        elif exc.retryable and attempts < 3:
            await rt.db.messages.update_one(query, {'$set': {'status': 'retry_wait', 'error_code': exc.code,
                'due_at': now() + timedelta(seconds=(5, 30, 120)[attempts-1] + random.uniform(0, 3))},
                '$unset': {'dispatch_started_at': ''}})
        else:
            await finish('failed', exc.code)


async def maintenance(rt):
    for instance in await rt.db.instances.find({'connection_state': {'$nin': ['creating', 'error', 'deleting']}}).to_list(100):
        try:
            state = await rt.provider.connection(instance['provider_ref'])
            changes = {'connection_state': state, 'observed_at': now(), 'last_error': None}
            if state != instance['connection_state'] and state in ('connected', 'disconnected'):
                changes['last_connected_at' if state == 'connected' else 'last_disconnected_at'] = now()
            await rt.db.instances.update_one({'_id': instance['_id'], 'tenant_id': instance['tenant_id'],
                'connection_state': {'$ne': 'deleting'}}, {'$set': changes})
        except ProviderError as exc:
            await rt.db.instances.update_one({'_id': instance['_id'], 'tenant_id': instance['tenant_id']}, {'$set': {'last_error': exc.code}})
    await rt.db.messages.update_many({'content_expires_at': {'$lte': now()}, 'content': {'$exists': True}}, {'$unset': {'content': ''}})


async def billing_loop(rt):
    from .billing import billing_tick
    while True:
        try:
            await billing_tick(rt)
        except Exception:
            print('{"level":"error","event":"billing_iteration_failed"}', flush=True)
        await asyncio.sleep(60)


async def main():
    config = Settings()
    try:
        static_destinations = load_destinations(config.whatsapp_outbound_webhooks.get_secret_value())
    except ValueError:
        print('{"level":"error","event":"callback_config_invalid"}', flush=True)
        static_destinations = {}
    rt = Runtime(config)
    registry = DestinationRegistry(rt, static_destinations)
    background_tasks = []
    last_check, cursor = 0.0, '0-0'
    try:
        await migrate(rt)
        background_tasks.append(asyncio.create_task(billing_loop(rt)))
        await migrate_callbacks(rt)
        background_tasks.append(asyncio.create_task(callback_loop(rt, registry)))
        while True:
            try:
                await rt.redis.set('worker:heartbeat', now().isoformat(), ex=90)
                pending = {'processed': False, '$or': [{'next_attempt_at': {'$lte': now()}}, {'next_attempt_at': {'$exists': False}}]}
                for event in await rt.db.gateway_events.find(pending).sort('created_at', 1).to_list(50):
                    if await process_event(rt, event):
                        if destinations := await registry.current():
                            await enqueue_callbacks(rt, event, destinations)
                        await rt.db.gateway_events.update_one({'_id': event['_id'], 'tenant_id': event['tenant_id']}, {'$set': {'processed': True}})
                    else:
                        expired = event['created_at'] < now() - timedelta(hours=1)
                        await rt.db.gateway_events.update_one({'_id': event['_id'], 'tenant_id': event['tenant_id']},
                            {'$set': {'processed': expired, 'outcome': 'unmatched' if expired else 'awaiting_correlation',
                                      'next_attempt_at': now() + timedelta(seconds=30)}})
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
