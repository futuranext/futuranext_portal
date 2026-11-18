"""Rotas internas chamadas pela API do portal, com licença e aceite lidos do schema futuranext."""
import asyncio
import json
from uuid import uuid4

import pytest
from backend.app.providers import ProviderError

from backend.app import callbacks

TOKEN = 'synthetic-portal-service-token-0123456789abcdef'
BOT_URL = 'https://bot.example.com/webhooks/whatsapp'


@pytest.fixture
def service(client, rt):
    rt.config.portal_service_token = type(rt.config.portal_service_token)(TOKEN)
    client.headers['Authorization'] = 'Bearer ' + TOKEN
    client.headers['X-Actor-Id'] = str(rt.pg.actor)
    # Instâncias semeadas não interessam aqui: cada empresa começa sem conexão.
    rt.pg.sql('DELETE FROM whatsapp.instances', fetch=False)
    return client


def base(tenant):
    return f'/service/v1/tenants/{tenant}/whatsapp'


def create(service, tenant):
    response = service.post(base(tenant) + '/instance', json={'company_name': 'Barbearia Teste'})
    assert response.status_code == 200, response.text
    return response.json()


def accept_terms(rt, tenant):
    rt.pg.sql('''INSERT INTO futuranext.whatsapp_consents (tenant_id, user_id, email, terms_version, expires_at, accepted_at)
                 VALUES (%s, %s, 'admin@example.test', '2026-11-17', now() + interval '1 day', now())''',
              (tenant, rt.pg.actor), fetch=False)


def test_requires_service_token(client, rt):
    path = base(rt.pg.tenant_a)
    assert client.get(path).status_code == 401
    rt.config.portal_service_token = type(rt.config.portal_service_token)(TOKEN)
    assert client.get(path, headers={'Authorization': 'Bearer wrong-token'}).status_code == 401
    assert client.get('/service/v1/tenants/not-a-uuid/whatsapp',
                      headers={'Authorization': 'Bearer ' + TOKEN}).status_code in (401, 422)
    rt.config.portal_service_token = type(rt.config.portal_service_token)('short')
    assert client.get(path, headers={'Authorization': 'Bearer short'}).status_code == 401


def test_unknown_or_unlicensed_company_cannot_create(service, rt):
    unknown = service.post(base(uuid4()) + '/instance', json={'company_name': 'X'})
    assert unknown.status_code == 409 and unknown.json()['error']['code'] == 'LICENSE_REQUIRED'
    unlicensed = service.post(base(rt.pg.unlicensed) + '/instance', json={'company_name': 'X'})
    assert unlicensed.status_code == 409
    rt.provider.create.assert_not_awaited()
    assert service.get(base(rt.pg.unlicensed)).json() == {'instance': None, 'webhook': None, 'keys': []}


def test_instance_is_created_once_and_qr_requires_terms(service, rt):
    tenant = rt.pg.tenant_a
    first = create(service, tenant)
    assert first['instance']['connection_state'] == 'disconnected'
    assert create(service, tenant)['instance']['id'] == first['instance']['id']
    rt.provider.create.assert_awaited_once()
    [stored] = rt.pg.sql('SELECT tenant_id, webhook_hash FROM whatsapp.instances')
    assert stored['tenant_id'] == tenant and stored['webhook_hash']

    rt.provider.connection.return_value = 'disconnected'
    rt.provider.connect.return_value = {'base64': 'data:image/png;base64,AAAA'}
    denied = service.post(base(tenant) + '/connect')
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'TERMS_REQUIRED'
    assert service.get(base(tenant) + '/qr').status_code == 409
    accept_terms(rt, tenant)
    other_user = service.post(base(tenant) + '/connect', headers={'X-Actor-Id': str(uuid4())})
    assert other_user.json()['error']['code'] == 'TERMS_REQUIRED'
    assert service.post(base(tenant) + '/connect').status_code == 202
    assert service.get(base(tenant) + '/qr').json()['base64'].startswith('data:image/png')
    other = service.get(base(rt.pg.tenant_b) + '/qr')
    assert other.status_code == 409 and other.json()['error']['code'] == 'INSTANCE_REQUIRED'
    assert rt.pg.sql("SELECT actor_id FROM whatsapp.audit_events WHERE action = 'instance.connect'") == [
        {'actor_id': f'portal:{rt.pg.actor}'}]


def test_failed_gateway_creation_is_retried(service, rt):
    rt.provider.create.side_effect = ProviderError('GATEWAY_UNAVAILABLE', retryable=True)
    failed = service.post(base(rt.pg.tenant_a) + '/instance', json={'company_name': 'Barbearia Teste'})
    assert failed.status_code == 503
    [stored] = rt.pg.sql('SELECT connection_state, last_error FROM whatsapp.instances')
    assert stored == {'connection_state': 'error', 'last_error': 'GATEWAY_UNAVAILABLE'}
    rt.provider.create.side_effect = None
    assert create(service, rt.pg.tenant_a)['instance']['connection_state'] == 'disconnected'
    rt.provider.delete.assert_awaited_once()


def test_webhook_secret_is_shown_once_and_feeds_worker(service, rt, work):
    tenant = rt.pg.tenant_a
    create(service, tenant)
    saved = service.put(base(tenant) + '/webhook', json={'url': BOT_URL, 'include_message': True}).json()
    secret = saved['webhook_secret']
    assert secret.startswith('whsec_') and saved['webhook']['url'] == BOT_URL
    assert secret not in service.get(base(tenant)).text
    assert secret not in str(rt.pg.sql('SELECT * FROM whatsapp.callback_destinations'))
    again = service.put(base(tenant) + '/webhook', json={'url': BOT_URL, 'include_message': False}).json()
    assert again['webhook_secret'] is None
    rotated = service.put(base(tenant) + '/webhook', json={'url': BOT_URL, 'rotate_secret': True}).json()
    assert rotated['webhook_secret'] not in (None, secret)

    [target] = work(callbacks.stored_destinations).values()
    assert (target.tenant_id, target.url, target.secret) == (str(tenant), BOT_URL, rotated['webhook_secret'])
    assert service.delete(base(tenant) + '/webhook').json()['webhook'] is None
    assert work(callbacks.stored_destinations) == {}
    # O contrato do portal (futuranext.whatsapp_webhooks) não é usado para guardar URL ou segredo.
    assert rt.pg.sql('SELECT count(*) AS total FROM futuranext.whatsapp_webhooks WHERE tenant_id = %s',
                     (tenant,))[0]['total'] == 0


@pytest.mark.parametrize('url', [
    'http://bot.example.com/hook', 'https://127.0.0.1/hook', 'https://bot.example.com:8443/hook',
    'https://bot.example.com/hook?token=1', 'https://user@bot.example.com/hook', 'https://localhost/hook',
])
def test_rejects_unsafe_webhook_urls(service, rt, url):
    create(service, rt.pg.tenant_a)
    assert service.put(base(rt.pg.tenant_a) + '/webhook', json={'url': url}).status_code == 422


def test_registry_merges_portal_and_operator_destinations(service, rt, work):
    create(service, rt.pg.tenant_a)
    service.put(base(rt.pg.tenant_a) + '/webhook', json={'url': BOT_URL})
    operator = {'tenant_id': '33333333-3333-4333-8333-333333333333', 'kind': 'bot',
                'instance_id': '22222222-2222-4222-8222-222222222222',
                'url': 'https://other.example.com/hook', 'secret': 'synthetic-' + 'x' * 32}
    static = callbacks.load_destinations(json.dumps([operator]))

    async def current(runtime):
        return await callbacks.DestinationRegistry(runtime, static).current()
    assert len(work(current)) == 2


def test_bot_key_sends_and_is_revoked(service, rt):
    tenant = rt.pg.tenant_a
    instance_id = create(service, tenant)['instance']['id']
    rt.pg.sql("UPDATE whatsapp.instances SET connection_state = 'connected'", fetch=False)
    issued = service.post(base(tenant) + '/keys', json={'name': 'Bot de atendimento'})
    assert issued.status_code == 201
    secret = issued.json()['api_key']
    assert secret not in service.get(base(tenant)).text
    assert secret not in str(rt.pg.sql('SELECT * FROM whatsapp.api_keys'))

    headers = {'Authorization': 'Bearer ' + secret, 'X-Actor-Id': ''}
    payload = {'instance_id': instance_id, 'phone': '5511999999999', 'message': 'Olá', 'idempotency_key': 'k1'}
    assert service.post('/api/v1/messages/send', json=payload, headers=headers).status_code == 202
    assert service.post('/api/v1/instances', json={'name': 'Outra'}, headers=headers).status_code == 403
    rt.pg.sql('INSERT INTO whatsapp.suppressions (tenant_id, phone_hash) VALUES (%s, %s)',
              (tenant, rt.digest(f'{tenant}:5511999999999')), fetch=False)
    blocked = service.post('/api/v1/messages/send', json={**payload, 'idempotency_key': 'k2'}, headers=headers)
    assert blocked.status_code == 403 and blocked.json()['error']['code'] == 'OPTED_OUT'

    key_id = service.get(base(tenant)).json()['keys'][0]['id']
    assert service.delete(f'{base(tenant)}/keys/{key_id}').status_code == 200
    assert service.delete(f'{base(tenant)}/keys/{key_id}').status_code == 404
    assert service.delete(f'{base(tenant)}/keys/nao-e-uuid').status_code == 404
    assert service.get('/api/v1/messages', headers=headers).status_code == 401


def test_key_limit_holds_under_concurrency(service, rt):
    import httpx
    tenant = rt.pg.tenant_a
    create(service, tenant)

    async def scenario():
        transport = httpx.ASGITransport(app=service.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test', headers=service.headers) as http:
            return await asyncio.gather(*(http.post(base(tenant) + '/keys', json={'name': f'k{index}'})
                                          for index in range(14)))
    codes = sorted(response.status_code for response in service.portal.call(scenario))
    assert codes.count(201) == 10 and codes.count(409) == 4
    assert rt.pg.sql('SELECT count(*) AS total FROM whatsapp.api_keys')[0]['total'] == 10


def test_company_b_service_calls_never_touch_company_a(service, rt):
    create(service, rt.pg.tenant_a)
    service.put(base(rt.pg.tenant_a) + '/webhook', json={'url': BOT_URL})
    other = service.get(base(rt.pg.tenant_b)).json()
    assert other == {'instance': None, 'webhook': None, 'keys': []}
    assert service.delete(base(rt.pg.tenant_b) + '/webhook').status_code == 409
    assert service.post(base(rt.pg.tenant_b) + '/keys', json={'name': 'x'}).status_code == 409
