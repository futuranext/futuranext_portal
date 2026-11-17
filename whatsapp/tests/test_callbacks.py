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

from backend.app import callbacks
from backend.app.callback_migration import migrate_callbacks
from backend.app.core import now

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


async def seed_event(rt, tenant=TENANT):
    await rt.db.instances.insert_one({'_id': INSTANCE, 'tenant_id': tenant, 'connection_state': 'connected'})
    event = {'_id': 'source-event', 'tenant_id': tenant, 'instance_id': INSTANCE, 'type': 'messages.upsert',
             'data': rt.seal({'phone': '5511999999999', 'message': 'olá', 'message_id': 'provider-id',
                              'other': 'canary'}), 'created_at': now(), 'expires_at': now() + timedelta(days=14)}
    await rt.db.gateway_events.insert_one(event)
    return event


@pytest.mark.parametrize('overrides', [
    {'url': 'http://api.example.com/events'}, {'url': 'https://api.example.com@evil.example.com/events'},
    {'url': 'https://127.0.0.1/events'}, {'url': 'https://api.example.com:8443/events'},
    {'url': PORTAL_URL + '?target=private'}, {'url': 'https://api.example.com/other'},
    {'include_message': True}, {'secret': 'short'}, {'instance_id': 'invalid'},
])
def test_rejects_bad_operator_config(overrides):
    with pytest.raises(ValueError, match='Configuração WHATSAPP_OUTBOUND_WEBHOOKS inválida'):
        registry(destination(**overrides))


def test_duplicate_and_legacy_configs():
    with pytest.raises(ValueError):
        registry(destination(), destination('bot', secret=destination()['secret']))
    with pytest.raises(ValueError):
        registry(destination(), destination('bot', tenant_id=PORTAL_TENANT))
    with pytest.raises(ValueError):
        registry(destination(), destination())
    assert registry(destination(tenant_id='local-company'))
    assert callbacks.load_destinations('') == {}


def test_durable_fanout_and_signed_payload(rt, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    seen = {}
    entries = {entry['kind']: entry for entry in (destination(), destination('bot', include_message=True))}

    def receive(request):
        hostname = request.headers['host']
        kind = 'futuranext' if hostname == 'api.example.com' else 'bot'
        assert request.url.host == '93.184.215.14'
        assert request.extensions['sni_hostname'] == hostname
        assert 'apikey' not in request.headers and 'cookie' not in request.headers
        payload = json.loads(request.content)
        expected = hmac.new(entries[kind]['secret'].encode(),
                            request.headers['x-timestamp'].encode() + b'.' + request.content,
                            hashlib.sha256).hexdigest()
        assert request.headers['x-signature'] == 'sha256=' + expected
        assert request.headers['x-event-id'] == payload['id']
        assert str(UUID(payload['id'])) == payload['id']
        seen[kind] = payload
        return httpx.Response(201, headers={'Set-Cookie': 'sensitive-cookie=canary; Path=/'})

    async def scenario():
        await migrate_callbacks(rt)
        await migrate_callbacks(rt)
        event = await seed_event(rt)
        targets = registry(*entries.values())
        await asyncio.gather(*(callbacks.enqueue_callbacks(rt, event, targets) for _ in range(3)))
        assert await rt.db.webhook_deliveries.count_documents({}) == 2
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            assert await callbacks.dispatch_callback(rt, http, targets)
            assert await callbacks.dispatch_callback(rt, http, targets)
            assert not await callbacks.dispatch_callback(rt, http, targets)
        assert await rt.db.webhook_deliveries.count_documents({'status': 'delivered', 'attempts': 1}) == 2
        stored = await rt.db.webhook_deliveries.find().to_list(2)
        assert 'canary' not in str(stored) and '5511999999999' not in str(stored)
        assert set(seen['futuranext']) == {'id', 'type', 'instance_id', 'occurred_at'}
        assert seen['futuranext']['instance_id'] == INSTANCE
        assert seen['futuranext']['type'] == 'message.received'
        assert seen['bot']['data'] == {'phone': '5511999999999', 'message': 'olá', 'message_id': 'provider-id'}
        assert seen['bot']['id'] == seen['futuranext']['id']

    asyncio.run(scenario())


def test_sends_no_content_unless_opted_in_and_stops_at_redirect(rt, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    received = []

    def receive(request):
        received.append(request)
        assert json.loads(request.content)['data'] == {}
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1/admin'})

    async def scenario():
        event = await seed_event(rt)
        unrelated = registry(destination('bot', tenant_id=PORTAL_TENANT))
        await callbacks.enqueue_callbacks(rt, event, unrelated)
        assert not await rt.db.webhook_deliveries.count_documents({})
        targets = registry(destination('bot'))
        await callbacks.enqueue_callbacks(rt, event, targets)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive), follow_redirects=False) as http:
            await callbacks.dispatch_callback(rt, http, targets)
        assert await rt.db.webhook_deliveries.find_one({'status': 'failed', 'http_status': 302})
        assert len(received) == 1

    asyncio.run(scenario())


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


def test_recovers_portal_safely_and_avoids_uncertain_bot_retry(rt, monkeypatch):
    monkeypatch.setattr(callbacks, '_resolve', AsyncMock(return_value='93.184.215.14'))
    attempts = []

    def receive(request):
        attempts.append((request.headers['host'], json.loads(request.content)))
        if len(attempts) < 3:
            raise httpx.ReadTimeout('test: timeout after sending', request=request)
        return httpx.Response(200)

    async def scenario():
        event = await seed_event(rt)
        targets = registry(destination(), destination('bot'))
        await callbacks.enqueue_callbacks(rt, event, targets)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            await callbacks.dispatch_callback(rt, http, targets)
            await callbacks.dispatch_callback(rt, http, targets)
            assert await rt.db.webhook_deliveries.find_one({'kind': 'bot', 'status': 'unknown', 'attempts': 1})
            assert await rt.db.webhook_deliveries.find_one({'kind': 'futuranext', 'status': 'retry_wait', 'attempts': 1})
            await rt.db.webhook_deliveries.update_one({'kind': 'futuranext'}, {'$set': {'due_at': now()}})
            await callbacks.dispatch_callback(rt, http, targets)
        assert await rt.db.webhook_deliveries.find_one({'kind': 'futuranext', 'status': 'delivered', 'attempts': 2})
        portal_payloads = [payload for host, payload in attempts if host == 'api.example.com']
        assert len(portal_payloads) == 2 and portal_payloads[0] == portal_payloads[1]

    asyncio.run(scenario())
