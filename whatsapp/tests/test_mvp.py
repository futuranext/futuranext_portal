import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import httpx

from backend.app.core import now
from backend.app.worker import dispatch_one
from backend.app.events import process_event, normalize_event
from backend.app.providers import EvolutionProvider, ProviderError

PHONE = '5511999999999'
PAYLOAD = {'instance_id': 'instance-a', 'phone': PHONE, 'message': 'Mensagem de teste', 'idempotency_key': 'test-1'}


def consent(admin):
    rt = admin.app.state.rt
    asyncio.run(rt.db.consent_requests.insert_one({'tenant_id': 'tenant-a', 'phone_hash': rt.digest('tenant-a:' + PHONE),
        'status': 'confirmed', 'delivery_channel': 'email_zoho', 'token_hash': 'synthetic-consent', 'confirmed_at': now()}))


def test_login_csrf_and_logout(client, rt):
    assert client.get('/api/v1/instances').status_code == 401
    response = client.post('/api/v1/auth/login', json={'email': 'admin@example.test', 'password': 'wrong'})
    assert response.status_code == 401
    response = client.post('/api/v1/auth/login', json={'email': 'admin@example.test', 'password': 'Test-password-123!'})
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
    assert client.post('/api/v1/instances', json={'name': 'Teste'}).status_code == 403
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    assert client.post('/api/v1/auth/logout').status_code == 204
    assert client.get('/api/v1/instances').status_code == 401


def test_cross_tenant_instances_and_qr_are_hidden(admin):
    assert [i['id'] for i in admin.get('/api/v1/instances').json()['items']] == ['instance-a']
    for suffix in ('connection', 'qr'):
        assert admin.get('/api/v1/instances/instance-b/' + suffix).status_code == 404
    assert admin.post('/api/v1/instances/instance-b/connect').status_code == 404
    assert admin.delete('/api/v1/instances/instance-b').status_code == 404


def test_idempotency_without_recipient_email_consent(admin, rt):
    first = admin.post('/api/v1/messages/send', json=PAYLOAD)
    assert first.status_code == 202 and first.json()['status'] == 'queued'
    repeat = admin.post('/api/v1/messages/send', json=PAYLOAD)
    assert repeat.status_code == 200 and first.json()['id'] == repeat.json()['id']
    assert admin.post('/api/v1/messages/send', json={**PAYLOAD, 'message': 'Outro texto'}).status_code == 409
    stored = asyncio.run(rt.db.messages.find_one({'_id': first.json()['id']}))
    assert 'Mensagem de teste' not in stored['content'] and PHONE not in str(stored)


def test_optout_cancels_queue_and_prevents_future_send(admin, rt):
    consent(admin)
    first = admin.post('/api/v1/messages/send', json=PAYLOAD).json()
    assert admin.post('/api/v1/opt-outs', json={'phone': PHONE}).status_code == 201
    assert admin.get('/api/v1/messages/' + first['id']).json()['status'] == 'cancelled'
    assert admin.post('/api/v1/messages/send', json={**PAYLOAD, 'idempotency_key': 'other'}).status_code == 403
    asyncio.run(dispatch_one(rt))
    rt.provider.send_text.assert_not_awaited()


def test_key_scope_revocation_and_secret_once(admin, rt):
    response = admin.post('/api/v1/api-keys', json={'name': 'Robô', 'scopes': ['messages:read'], 'instance_ids': ['instance-a']})
    assert response.status_code == 201
    key = response.json()
    assert key['secret'] not in admin.get('/api/v1/api-keys').text
    cookie = dict(admin.cookies)
    admin.cookies.clear()
    admin.headers['Authorization'] = 'Bearer ' + key['secret']
    assert admin.get('/api/v1/messages').status_code == 200
    assert admin.post('/api/v1/messages/send', json=PAYLOAD).status_code == 403
    assert admin.get('/api/v1/instances/instance-a/qr').status_code == 403
    del admin.headers['Authorization']
    admin.cookies.update(cookie)
    assert admin.delete('/api/v1/api-keys/' + key['id']).status_code == 204
    admin.cookies.clear()
    admin.headers['Authorization'] = 'Bearer ' + key['secret']
    assert admin.get('/api/v1/messages').status_code == 401


def test_key_cannot_target_other_tenant(admin):
    assert admin.post('/api/v1/api-keys', json={'name': 'Bad', 'scopes': ['messages:read'], 'instance_ids': ['instance-b']}).status_code == 404


def test_worker_success_is_sent_not_delivered(admin, rt):
    consent(admin)
    result = admin.post('/api/v1/messages/send', json=PAYLOAD).json()
    asyncio.run(dispatch_one(rt))
    message = admin.get('/api/v1/messages/' + result['id']).json()
    assert message['status'] == 'sent' and message['provider_message_id'] == 'provider-message-1'


def test_timeout_is_unknown_and_not_retried(admin, rt):
    consent(admin)
    result = admin.post('/api/v1/messages/send', json=PAYLOAD).json()
    rt.provider.send_text.side_effect = ProviderError('TIMEOUT', uncertain=True)
    asyncio.run(dispatch_one(rt))
    asyncio.run(dispatch_one(rt))
    assert admin.get('/api/v1/messages/' + result['id']).json()['status'] == 'unknown'
    assert rt.provider.send_text.await_count == 1


def test_expired_started_lease_is_unknown(admin, rt):
    consent(admin)
    result = admin.post('/api/v1/messages/send', json=PAYLOAD).json()
    asyncio.run(rt.db.messages.update_one({'_id': result['id']}, {'$set': {'status': 'processing', 'lease_until': now()-timedelta(seconds=1), 'dispatch_started_at': now()}}))
    asyncio.run(dispatch_one(rt))
    assert admin.get('/api/v1/messages/' + result['id']).json()['status'] == 'unknown'
    rt.provider.send_text.assert_not_awaited()


def test_webhook_auth_dedup_and_status_precedence(admin, rt):
    consent(admin)
    result = admin.post('/api/v1/messages/send', json=PAYLOAD).json()
    asyncio.run(dispatch_one(rt))
    envelope = {'instance': 'wp_instance-a', 'event': 'messages.update', 'apikey': 'NEVER_STORE', 'data': {'keyId': 'provider-message-1', 'status': 'READ'}}
    url = '/internal/webhooks/evolution/instance-a'
    assert admin.post(url, json=envelope).status_code == 401
    assert admin.post(url, json={**envelope, 'instance': 'wp_instance-b'}, headers={'X-Webhook-Secret': 'webhook-test'}).status_code == 422
    for _ in range(2):
        assert admin.post(url, json=envelope, headers={'X-Webhook-Secret': 'webhook-test'}).status_code == 200
    assert asyncio.run(rt.db.gateway_events.count_documents({})) == 1
    event = asyncio.run(rt.db.gateway_events.find_one({}))
    assert 'NEVER_STORE' not in str(rt.unseal(event['data']))
    asyncio.run(process_event(rt, event))
    event['data'] = rt.seal({'message_id': 'provider-message-1', 'status': 'sent'})
    asyncio.run(process_event(rt, event))
    assert admin.get('/api/v1/messages/' + result['id']).json()['status'] == 'read'


def test_received_stop_creates_suppression(admin, rt):
    event = {'tenant_id': 'tenant-a', 'instance_id': 'instance-a', 'type': 'messages.upsert',
             'data': rt.seal({'message_id': 'inbound-1', 'phone': PHONE, 'message': 'SAIR'})}
    asyncio.run(process_event(rt, event))
    asyncio.run(process_event(rt, event))
    assert asyncio.run(rt.db.messages.count_documents({'direction': 'inbound'})) == 1
    assert asyncio.run(rt.db.suppressions.count_documents({})) == 1


def test_qr_stays_ephemeral_and_private(admin, rt):
    asyncio.run(rt.db.instances.update_one({'_id': 'instance-a'}, {'$set': {'connection_state': 'disconnected'}}))
    rt.provider.connect.return_value = {'base64': 'data:image/png;base64,dGVzdA=='}
    assert admin.post('/api/v1/instances/instance-a/connect').status_code == 202
    response = admin.get('/api/v1/instances/instance-a/qr')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    doc = asyncio.run(rt.db.instances.find_one({'_id': 'instance-a'}))
    assert 'dGVzdA==' not in str(doc)
    rt.redis.expiry['qr:instance-a'] = 0
    assert admin.get('/api/v1/instances/instance-a/qr').status_code == 409


def test_validation_does_not_echo_password_or_body(client):
    response = client.post('/api/v1/auth/login', json={'email': 'x', 'password': 'PRIVATE_SECRET', 'unexpected': True})
    assert response.status_code == 422 and 'PRIVATE_SECRET' not in response.text
    assert client.post('/api/v1/auth/login', content=b'x' * 1048577).status_code == 413


def test_adapter_exact_request_and_ambiguous_response():
    async def run():
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'key': {'id': 'abc'}})
        async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(handler)) as http:
            provider = EvolutionProvider(http)
            await provider.send_text('wp_test', PHONE, 'Texto')
            assert requests[0].url.path == '/message/sendText/wp_test'
            assert b'"number"' in requests[0].content and b'"text"' in requests[0].content
        def timeout(request):
            raise httpx.ReadTimeout('sensitive-not-propagated')
        async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(timeout)) as http:
            try:
                await EvolutionProvider(http).send_text('wp_test', PHONE, 'Texto')
            except ProviderError as exc:
                assert exc.uncertain and 'sensitive' not in str(exc)
            else:
                raise AssertionError('Timeout não tratado')
    asyncio.run(run())


def test_csrf_wrong_origin_and_login_rate_limit(client):
    body = {'email': 'admin@example.test', 'password': 'incorrect'}
    assert client.post('/api/v1/auth/login', json=body, headers={'Origin': 'https://attacker.test'}).status_code == 403
    for _ in range(10):
        assert client.post('/api/v1/auth/login', json=body).status_code == 401
    assert client.post('/api/v1/auth/login', json=body).status_code == 429


def test_lost_stream_signal_keeps_durable_outbox(admin, rt):
    consent(admin)
    rt.redis.xadd = AsyncMock(side_effect=RuntimeError('Redis signal lost'))
    response = admin.post('/api/v1/messages/send', json=PAYLOAD)
    assert response.status_code == 202
    asyncio.run(dispatch_one(rt))
    assert admin.get('/api/v1/messages/' + response.json()['id']).json()['status'] == 'sent'


def test_safe_connection_failure_schedules_retry(admin, rt):
    consent(admin)
    result = admin.post('/api/v1/messages/send', json=PAYLOAD).json()
    rt.provider.send_text.side_effect = ProviderError('CONNECT_FAILED', retryable=True)
    asyncio.run(dispatch_one(rt))
    doc = asyncio.run(rt.db.messages.find_one({'_id': result['id']}))
    assert doc['status'] == 'retry_wait' and 'dispatch_started_at' not in doc
    assert doc['attempts'] == 1 and doc['due_at'] > now()


def test_key_instance_restriction_hides_message(admin, rt):
    consent(admin)
    mid = admin.post('/api/v1/messages/send', json=PAYLOAD).json()['id']
    asyncio.run(rt.db.messages.update_one({'_id': mid}, {'$set': {'tenant_id': 'tenant-b', 'instance_id': 'instance-b'}}))
    assert admin.get('/api/v1/messages/' + mid).status_code == 404
    assert admin.get('/api/v1/messages').json()['items'] == []


def test_redis_failure_blocks_authenticated_requests(admin, rt):
    rt.redis.eval = AsyncMock(side_effect=RuntimeError('Unavailable'))
    assert admin.get('/api/v1/instances').status_code == 503
