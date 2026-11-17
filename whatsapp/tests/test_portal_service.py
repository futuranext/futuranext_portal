import asyncio
import json

import pytest

from backend.app import callbacks
from backend.app.core import now

TOKEN = 'synthetic-portal-service-token-0123456789abcdef'
TENANT = '11111111-1111-4111-8111-111111111111'
OTHER = '44444444-4444-4444-8444-444444444444'
BASE = f'/service/v1/tenants/{TENANT}/whatsapp'
BOT_URL = 'https://bot.example.com/webhooks/whatsapp'


@pytest.fixture
def service(client, rt):
    rt.config.portal_service_token = type(rt.config.portal_service_token)(TOKEN)
    client.headers['Authorization'] = 'Bearer ' + TOKEN
    return client


def create(service):
    response = service.post(BASE + '/instance', json={'company_name': 'Barbearia Teste'})
    assert response.status_code == 200
    return response.json()


def test_requires_service_token(client, rt):
    assert client.get(BASE).status_code == 401
    rt.config.portal_service_token = type(rt.config.portal_service_token)(TOKEN)
    assert client.get(BASE, headers={'Authorization': 'Bearer wrong-token'}).status_code == 401
    assert client.get('/service/v1/tenants/not-a-uuid/whatsapp',
                      headers={'Authorization': 'Bearer ' + TOKEN}).status_code in (401, 422)


def test_short_configured_token_is_rejected(client, rt):
    rt.config.portal_service_token = type(rt.config.portal_service_token)('short')
    assert client.get(BASE, headers={'Authorization': 'Bearer short'}).status_code == 401


def test_instance_is_created_once_and_qr_is_tenant_scoped(service, rt):
    first = create(service)
    assert first['instance']['connection_state'] == 'disconnected'
    assert create(service)['instance']['id'] == first['instance']['id']
    rt.provider.create.assert_awaited_once()
    instance_id = first['instance']['id']
    stored = asyncio.run(rt.db.instances.find_one({'_id': instance_id}))
    assert stored['tenant_id'] == TENANT and stored['webhook_hash']

    rt.provider.connection.return_value = 'disconnected'
    rt.provider.connect.return_value = {'base64': 'data:image/png;base64,AAAA'}
    assert service.post(BASE + '/connect').status_code == 202
    assert service.get(BASE + '/qr').json()['base64'].startswith('data:image/png')
    assert service.get(f'/service/v1/tenants/{OTHER}/whatsapp/qr').status_code == 409


def test_failed_gateway_creation_is_retried(service, rt):
    from backend.app.providers import ProviderError
    rt.provider.create.side_effect = ProviderError('GATEWAY_UNAVAILABLE', retryable=True)
    assert service.post(BASE + '/instance', json={'company_name': 'Barbearia Teste'}).status_code == 503
    rt.provider.create.side_effect = None
    assert create(service)['instance']['connection_state'] == 'disconnected'
    rt.provider.delete.assert_awaited_once()


def test_webhook_secret_is_shown_once_and_feeds_worker(service, rt):
    create(service)
    saved = service.put(BASE + '/webhook', json={'url': BOT_URL, 'include_message': True}).json()
    secret = saved['webhook_secret']
    assert secret.startswith('whsec_') and saved['webhook']['url'] == BOT_URL
    assert secret not in service.get(BASE).text
    again = service.put(BASE + '/webhook', json={'url': BOT_URL, 'include_message': False}).json()
    assert again['webhook_secret'] is None
    rotated = service.put(BASE + '/webhook', json={'url': BOT_URL, 'rotate_secret': True}).json()
    assert rotated['webhook_secret'] not in (None, secret)

    destinations = asyncio.run(callbacks.stored_destinations(rt))
    [target] = destinations.values()
    assert (target.tenant_id, target.url, target.secret) == (TENANT, BOT_URL, rotated['webhook_secret'])
    assert service.delete(BASE + '/webhook').json()['webhook'] is None
    assert asyncio.run(callbacks.stored_destinations(rt)) == {}


@pytest.mark.parametrize('url', [
    'http://bot.example.com/hook', 'https://127.0.0.1/hook', 'https://bot.example.com:8443/hook',
    'https://bot.example.com/hook?token=1', 'https://user@bot.example.com/hook', 'https://localhost/hook',
])
def test_rejects_unsafe_webhook_urls(service, url):
    create(service)
    response = service.put(BASE + '/webhook', json={'url': url})
    assert response.status_code == 422


def test_registry_merges_portal_and_operator_destinations(service, rt):
    create(service)
    service.put(BASE + '/webhook', json={'url': BOT_URL})
    operator = {'tenant_id': '33333333-3333-4333-8333-333333333333', 'kind': 'bot',
                'instance_id': '22222222-2222-4222-8222-222222222222',
                'url': 'https://other.example.com/hook', 'secret': 'synthetic-' + 'x' * 32}
    static = callbacks.load_destinations(json.dumps([operator]))
    registry = callbacks.DestinationRegistry(rt, static)
    assert len(asyncio.run(registry.current())) == 2


def test_bot_key_sends_without_recipient_email_consent(service, rt):
    instance_id = create(service)['instance']['id']
    asyncio.run(rt.db.instances.update_one({'_id': instance_id}, {'$set': {'connection_state': 'connected'}}))
    issued = service.post(BASE + '/keys', json={'name': 'Bot de atendimento'})
    assert issued.status_code == 201
    secret = issued.json()['api_key']
    assert secret not in service.get(BASE).text

    bot = service
    bot.headers['Authorization'] = 'Bearer ' + secret
    payload = {'instance_id': instance_id, 'phone': '5511999999999', 'message': 'Olá', 'idempotency_key': 'k1'}
    assert bot.post('/api/v1/messages/send', json=payload).status_code == 202
    assert bot.post('/api/v1/instances', json={'name': 'Outra'}).status_code == 403
    assert bot.post('/api/v1/opt-outs', json={'phone': '5511999999999'}).status_code == 403
    asyncio.run(rt.db.suppressions.insert_one({'tenant_id': TENANT,
        'phone_hash': rt.digest(f'{TENANT}:5511999999999'), 'opted_out_at': now()}))
    blocked = bot.post('/api/v1/messages/send', json={**payload, 'idempotency_key': 'k2'})
    assert blocked.status_code == 403 and blocked.json()['error']['code'] == 'OPTED_OUT'

    bot.headers['Authorization'] = 'Bearer ' + TOKEN
    key_id = service.get(BASE).json()['keys'][0]['id']
    assert service.delete(f'{BASE}/keys/{key_id}').status_code == 200
    bot.headers['Authorization'] = 'Bearer ' + secret
    assert bot.get('/api/v1/messages').status_code == 401


def test_webhook_delivery_uses_portal_destination(service, rt):
    instance_id = create(service)['instance']['id']
    service.put(BASE + '/webhook', json={'url': BOT_URL, 'include_message': True})
    destinations = asyncio.run(callbacks.stored_destinations(rt))
    event = {'_id': 'source', 'tenant_id': TENANT, 'instance_id': instance_id, 'type': 'messages.upsert',
             'created_at': now(), 'expires_at': now()}
    asyncio.run(callbacks.enqueue_callbacks(rt, event, destinations))
    delivery = asyncio.run(rt.db.webhook_deliveries.find_one({'tenant_id': TENANT}))
    assert delivery['kind'] == 'bot' and delivery['event_type'] == 'message.received'
