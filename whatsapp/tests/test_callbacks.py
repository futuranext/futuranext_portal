"""Entrega assinada de eventos aos bots e ao portal: fila PostgreSQL, HMAC, retries e SSRF."""
import asyncio
import hashlib
import hmac
import json
import socket
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from backend.app.core import now
from conftest import INSTANCE_A

from backend.app import callbacks

INSTANCE = '22222222-2222-4222-8222-222222222222'
TENANT = '33333333-3333-4333-8333-333333333333'
PORTAL_TENANT = '11111111-1111-4111-8111-111111111111'
PORTAL_URL = f'https://api.example.com/api/v1/integrations/whatsapp/{PORTAL_TENANT}/events'
BOT_URL = 'https://bot.example.com/webhook/whatsapp'


def destination(kind='futuranext', **overrides):
    return {'tenant_id': TENANT, 'instance_id': INSTANCE, 'kind': kind,
            'url': PORTAL_URL if kind == 'futuranext' else BOT_URL,
            'secret': 'synthetic-config-test-value-for-' + kind, **overrides}


def registry(*entries):
    return callbacks.load_destinations(json.dumps(entries))


def targets_for(rt, *kinds, **overrides):
    """Destinos do operador apontando para a instância semeada da empresa A."""
    scope = {'tenant_id': str(rt.pg.tenant_a), 'instance_id': str(INSTANCE_A)}
    return registry(*(destination(kind, **scope, **overrides.get(kind, {})) for kind in kinds))


def seed_event(rt):
    data = rt.seal({'phone': '5511999999999', 'message': 'olá', 'message_id': 'provider-id', 'other': 'canary'})
    rt.pg.sql('''INSERT INTO whatsapp.gateway_events (event_key, tenant_id, instance_id, type, data, processed, expires_at)
                 VALUES (%s, %s, %s, 'messages.upsert', %s, true, %s)''',
              ('a' * 64, rt.pg.tenant_a, INSTANCE_A, data, now() + timedelta(days=14)), fetch=False)
    return rt.pg.sql('SELECT * FROM whatsapp.gateway_events')[0]


async def enqueue(runtime, event, targets, times=1):
    async def once():
        async with runtime.db.tx() as conn:
            await callbacks.enqueue_callbacks(conn, event, targets)
    await asyncio.gather(*(once() for _ in range(times)))


def deliveries(rt, where='true', params=()):
    return rt.pg.sql(f'SELECT * FROM whatsapp.webhook_deliveries WHERE {where} ORDER BY kind', params)


@pytest.mark.parametrize('overrides', [
    {'url': 'http://api.example.com/events'}, {'url': 'https://api.example.com@evil.example.com/events'},
    {'url': 'https://127.0.0.1/events'}, {'url': 'https://api.example.com:8443/events'},
    {'url': PORTAL_URL + '?target=private'}, {'url': 'https://api.example.com/other'},
    {'include_message': True}, {'secret': 'short'}, {'instance_id': 'invalid'}, {'tenant_id': 'local-company'},
])
def test_rejects_bad_operator_config(overrides):
    with pytest.raises(ValueError, match='Configuração WHATSAPP_OUTBOUND_WEBHOOKS inválida'):
        registry(destination(**overrides))


def test_duplicate_configs():
    with pytest.raises(ValueError):
        registry(destination(), destination('bot', secret=destination()['secret']))
    with pytest.raises(ValueError):
        registry(destination(), destination('bot', tenant_id=PORTAL_TENANT))
    with pytest.raises(ValueError):
        registry(destination(), destination())
    assert callbacks.load_destinations('') == {}


def test_durable_fanout_and_signed_payload(rt, work, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    seen = {}
    targets = targets_for(rt, 'futuranext', 'bot', bot={'include_message': True})
    secrets = {target.kind: target.secret for target in targets.values()}

    def receive(request):
        hostname = request.headers['host']
        kind = 'futuranext' if hostname == 'api.example.com' else 'bot'
        assert request.url.host == '93.184.215.14'
        assert request.extensions['sni_hostname'] == hostname
        assert 'apikey' not in request.headers and 'cookie' not in request.headers
        payload = json.loads(request.content)
        expected = hmac.new(secrets[kind].encode(), request.headers['x-timestamp'].encode() + b'.' + request.content,
                            hashlib.sha256).hexdigest()
        assert request.headers['x-signature'] == 'sha256=' + expected
        assert request.headers['x-event-id'] == payload['id'] and str(UUID(payload['id'])) == payload['id']
        seen[kind] = payload
        return httpx.Response(201, headers={'Set-Cookie': 'sensitive-cookie=canary; Path=/'})

    event = seed_event(rt)

    async def scenario(runtime):
        await enqueue(runtime, event, targets, times=3)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            assert await callbacks.dispatch_callback(runtime, http, targets)
            assert await callbacks.dispatch_callback(runtime, http, targets)
            assert not await callbacks.dispatch_callback(runtime, http, targets)

    work(scenario)
    stored = deliveries(rt)
    assert [(row['status'], row['attempts']) for row in stored] == [('delivered', 1), ('delivered', 1)]
    assert 'canary' not in str(stored) and '5511999999999' not in str(stored)
    assert set(seen['futuranext']) == {'id', 'type', 'instance_id', 'occurred_at'}
    assert seen['futuranext']['instance_id'] == str(INSTANCE_A) and seen['futuranext']['type'] == 'message.received'
    assert seen['bot']['data'] == {'phone': '5511999999999', 'message': 'olá', 'message_id': 'provider-id'}
    assert seen['bot']['id'] == seen['futuranext']['id']


def test_sends_no_content_unless_opted_in_and_stops_at_redirect(rt, work, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    received = []

    def receive(request):
        received.append(request)
        assert json.loads(request.content)['data'] == {}
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1/admin'})

    event = seed_event(rt)
    unrelated = registry(destination('bot', tenant_id=str(rt.pg.tenant_b)))
    targets = targets_for(rt, 'bot')

    async def scenario(runtime):
        await enqueue(runtime, event, unrelated)
        assert not deliveries(rt)
        await enqueue(runtime, event, targets)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive), follow_redirects=False) as http:
            await callbacks.dispatch_callback(runtime, http, targets)

    work(scenario)
    assert deliveries(rt, "status = 'failed' AND http_status = 302")
    assert len(received) == 1


def test_retries_with_backoff_then_gives_up(rt, work, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    calls = []

    def busy(request):
        calls.append(request)
        return httpx.Response(429)

    event = seed_event(rt)
    targets = targets_for(rt, 'bot')

    async def scenario(runtime):
        await enqueue(runtime, event, targets)
        async with httpx.AsyncClient(transport=httpx.MockTransport(busy)) as http:
            for attempt, delay in enumerate(callbacks.RETRY_SECONDS, start=1):
                assert await callbacks.dispatch_callback(runtime, http, targets)
                [row] = deliveries(rt)
                assert (row['status'], row['attempts']) == ('retry_wait', attempt)
                assert timedelta(seconds=delay - 2) < row['due_at'] - now() <= timedelta(seconds=delay)
                assert not await callbacks.dispatch_callback(runtime, http, targets)  # ainda não venceu
                rt.pg.sql('UPDATE whatsapp.webhook_deliveries SET due_at = now()', fetch=False)
            assert await callbacks.dispatch_callback(runtime, http, targets)

    work(scenario)
    [row] = deliveries(rt)
    assert (row['status'], row['attempts'], row['http_status']) == ('failed', 4, 429)
    assert len(calls) == 4


def test_recovers_portal_safely_and_avoids_uncertain_bot_retry(rt, work, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    attempts = []

    def receive(request):
        attempts.append((request.headers['host'], json.loads(request.content)))
        if len(attempts) < 3:
            raise httpx.ReadTimeout('test: timeout after sending', request=request)
        return httpx.Response(200)

    event = seed_event(rt)
    targets = targets_for(rt, 'futuranext', 'bot')

    async def scenario(runtime):
        await enqueue(runtime, event, targets)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            await callbacks.dispatch_callback(runtime, http, targets)
            await callbacks.dispatch_callback(runtime, http, targets)
            assert deliveries(rt, "kind = 'bot' AND status = 'unknown' AND attempts = 1")
            assert deliveries(rt, "kind = 'futuranext' AND status = 'retry_wait' AND attempts = 1")
            rt.pg.sql("UPDATE whatsapp.webhook_deliveries SET due_at = now() WHERE kind = 'futuranext'", fetch=False)
            await callbacks.dispatch_callback(runtime, http, targets)

    work(scenario)
    assert deliveries(rt, "kind = 'futuranext' AND status = 'delivered' AND attempts = 2")
    portal_payloads = [payload for host, payload in attempts if host == 'api.example.com']
    assert len(portal_payloads) == 2 and portal_payloads[0] == portal_payloads[1]


def test_restart_recovers_expired_delivery_leases(rt, work):
    event = seed_event(rt)
    targets = targets_for(rt, 'futuranext', 'bot')
    work(enqueue, event, targets)
    rt.pg.sql("UPDATE whatsapp.webhook_deliveries SET status = 'processing', attempts = 1, "
              "lease_owner = gen_random_uuid(), lease_until = now() - interval '1 second'", fetch=False)
    work(callbacks.recover_deliveries)
    assert deliveries(rt, "kind = 'futuranext' AND status = 'retry_wait' AND error_code = 'WORKER_INTERRUPTED'")
    assert deliveries(rt, "kind = 'bot' AND status = 'unknown' AND error_code = 'WORKER_INTERRUPTED'")


def test_concurrent_workers_deliver_each_event_once(rt, work, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    received = []

    async def receive(request):
        received.append(request.headers['x-event-id'])
        await asyncio.sleep(0.01)
        return httpx.Response(200)

    targets = targets_for(rt, 'bot')
    keys = [f'{index:064x}' for index in range(12)]
    for key in keys:
        rt.pg.sql('''INSERT INTO whatsapp.gateway_events (event_key, tenant_id, instance_id, type, data, processed, expires_at)
                     VALUES (%s, %s, %s, 'connection.update', %s, true, now() + interval '1 day')''',
                  (key, rt.pg.tenant_a, INSTANCE_A, rt.seal({'state': 'connected'})), fetch=False)
    events = rt.pg.sql('SELECT * FROM whatsapp.gateway_events')

    async def scenario(runtime):
        for event in events:
            await enqueue(runtime, event, targets)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            async def drain():
                while await callbacks.dispatch_callback(runtime, http, targets):
                    pass
            await asyncio.gather(*(drain() for _ in range(4)))

    work(scenario)
    assert len(received) == len(set(received)) == len(keys)
    assert {row['status'] for row in deliveries(rt)} == {'delivered'}


def test_unlicensed_company_gets_no_callbacks(rt, work, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    event = seed_event(rt)
    targets = targets_for(rt, 'bot')
    work(enqueue, event, targets)
    rt.pg.sql('UPDATE futuranext.tenant_products SET active = false WHERE tenant_id = %s', (rt.pg.tenant_a,), fetch=False)
    try:
        async def scenario(runtime):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as http:
                await callbacks.dispatch_callback(runtime, http, targets)
        work(scenario)
    finally:
        rt.pg.sql('UPDATE futuranext.tenant_products SET active = true WHERE tenant_id = %s', (rt.pg.tenant_a,), fetch=False)
    assert deliveries(rt, "status = 'cancelled' AND error_code = 'LICENSE_INACTIVE'")


def test_pins_only_public_addresses(monkeypatch):
    def answer(address):
        return (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))

    async def scenario():
        lookup = AsyncMock(return_value=[answer('93.184.215.14'), answer('169.254.169.254')])
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', lookup)
        with pytest.raises(ValueError):
            await callbacks._resolve('bot.example.com')
        lookup.return_value = [answer('::ffff:127.0.0.1')]
        with pytest.raises(ValueError):
            await callbacks._resolve('bot.example.com')
        lookup.return_value = [answer('93.184.215.14')]
        assert await callbacks._resolve('bot.example.com') == '93.184.215.14'

    asyncio.run(scenario())
