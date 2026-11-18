"""API pública (chave wp_), fila de envio, eventos da Evolution e opt-out, com PostgreSQL real."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
from backend.app.core import now
from backend.app.events import normalize_event
from backend.app.providers import EvolutionProvider, ProviderError
from backend.app.worker import dispatch_one, process_events
from conftest import INSTANCE_A, INSTANCE_B, WEBHOOK_SECRET, issue_key

PHONE = '5511999999999'
PAYLOAD = {'instance_id': str(INSTANCE_A), 'phone': PHONE, 'message': 'Mensagem de teste', 'idempotency_key': 'test-1'}
HOOK = f'/internal/webhooks/evolution/{INSTANCE_A}'


class NoRegistry:
    async def current(self):
        return {}


def deliver(client, event, data, **extra):
    return client.post(HOOK, json={'instance': f'wp_{INSTANCE_A}', 'event': event, 'data': data, **extra},
                       headers={'X-Webhook-Secret': WEBHOOK_SECRET})


def test_requires_valid_key_of_licensed_company(client, rt):
    assert client.get('/api/v1/instances').status_code == 401
    assert client.get('/api/v1/instances', headers={'Authorization': 'Bearer wp_invalida'}).status_code == 401
    revoked = issue_key(rt, rt.pg.tenant_a, [INSTANCE_A], revoked_at=now())
    expired = issue_key(rt, rt.pg.tenant_a, [INSTANCE_A], expires_at=now() - timedelta(seconds=1))
    for secret in (revoked, expired):
        assert client.get('/api/v1/instances', headers={'Authorization': 'Bearer ' + secret}).status_code == 401
    # Licença encerrada no portal: a mesma chave deixa de funcionar sem nenhuma ação no serviço.
    secret = issue_key(rt, rt.pg.tenant_a, [INSTANCE_A])
    headers = {'Authorization': 'Bearer ' + secret}
    assert client.get('/api/v1/instances', headers=headers).status_code == 200
    rt.pg.sql("UPDATE futuranext.tenant_products SET active = false WHERE tenant_id = %s", (rt.pg.tenant_a,), fetch=False)
    try:
        assert client.get('/api/v1/instances', headers=headers).status_code == 401
    finally:
        rt.pg.sql("UPDATE futuranext.tenant_products SET active = true WHERE tenant_id = %s", (rt.pg.tenant_a,), fetch=False)


def test_scopes_and_tenant_selection(bot):
    assert bot.get('/api/v1/instances', headers={'X-Tenant-ID': 'qualquer'}).status_code == 403
    for method, path, body in (('post', '/api/v1/instances', {'name': 'Outra'}),
                               ('post', f'/api/v1/instances/{INSTANCE_A}/connect', None),
                               ('get', f'/api/v1/instances/{INSTANCE_A}/qr', None),
                               ('delete', f'/api/v1/instances/{INSTANCE_A}', None),
                               ('post', '/api/v1/opt-outs', {'phone': PHONE}),
                               ('get', '/api/v1/api-keys', None), ('get', '/api/v1/dashboard', None)):
        response = getattr(bot, method)(path, **({'json': body} if body else {}))
        assert response.status_code == 403, path


def test_tenant_a_cannot_read_tenant_b(bot, rt):
    assert [item['id'] for item in bot.get('/api/v1/instances').json()['items']] == [str(INSTANCE_A)]
    assert bot.get(f'/api/v1/instances/{INSTANCE_B}/connection').status_code == 404
    assert bot.get('/api/v1/instances/nao-e-uuid/connection').status_code == 404
    assert bot.post('/api/v1/messages/send', json={**PAYLOAD, 'instance_id': str(INSTANCE_B)}).status_code == 404
    other = issue_key(rt, rt.pg.tenant_b, [INSTANCE_B])
    sent = bot.post('/api/v1/messages/send', json={**PAYLOAD, 'instance_id': str(INSTANCE_B)},
                    headers={'Authorization': 'Bearer ' + other})
    assert sent.status_code == 202
    assert bot.get('/api/v1/messages/' + sent.json()['id']).status_code == 404
    assert bot.get('/api/v1/messages').json()['items'] == []
    assert bot.get('/api/v1/messages', params={'cursor': sent.json()['id']}).status_code == 422


def test_idempotency_and_encrypted_content(bot, rt):
    first = bot.post('/api/v1/messages/send', json=PAYLOAD)
    assert first.status_code == 202 and first.json()['status'] == 'queued'
    assert first.headers['location'] == '/api/v1/messages/' + first.json()['id']
    repeat = bot.post('/api/v1/messages/send', json=PAYLOAD)
    assert repeat.status_code == 200 and repeat.json()['id'] == first.json()['id']
    conflict = bot.post('/api/v1/messages/send', json={**PAYLOAD, 'message': 'Outro texto'})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    [stored] = rt.pg.sql('SELECT * FROM whatsapp.messages')
    assert 'Mensagem de teste' not in str(stored) and PHONE not in str(stored)
    listed = bot.get('/api/v1/messages', params={'phone': PHONE}).json()['items']
    assert [item['message'] for item in listed] == ['Mensagem de teste']


def test_concurrent_requests_with_same_key_create_one_message(bot, rt):
    async def scenario():
        transport = httpx.ASGITransport(app=bot.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test', headers=bot.headers) as http:
            return await asyncio.gather(*(http.post('/api/v1/messages/send', json=PAYLOAD) for _ in range(8)))
    responses = bot.portal.call(scenario)
    assert {response.status_code for response in responses} <= {200, 202}
    assert len({response.json()['id'] for response in responses}) == 1
    assert rt.pg.sql('SELECT count(*) AS total FROM whatsapp.messages')[0]['total'] == 1


def test_not_connected_instance_rejects_send(bot, rt):
    rt.pg.sql("UPDATE whatsapp.instances SET connection_state = 'disconnected' WHERE id = %s", (INSTANCE_A,), fetch=False)
    response = bot.post('/api/v1/messages/send', json=PAYLOAD)
    assert response.status_code == 409 and response.json()['error']['code'] == 'NOT_CONNECTED'


def test_worker_success_is_sent_not_delivered(bot, rt, work):
    result = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    work(dispatch_one)
    message = bot.get('/api/v1/messages/' + result['id']).json()
    assert message['status'] == 'sent' and message['provider_message_id'] == 'provider-message-1'
    rt.provider.send_text.assert_awaited_once_with(f'wp_{INSTANCE_A}', PHONE, 'Mensagem de teste')
    work(dispatch_one)
    rt.provider.send_text.assert_awaited_once()


def test_timeout_is_unknown_and_not_retried(bot, rt, work):
    result = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    rt.provider.send_text.side_effect = ProviderError('TIMEOUT', uncertain=True)
    work(dispatch_one)
    work(dispatch_one)
    assert bot.get('/api/v1/messages/' + result['id']).json()['status'] == 'unknown'
    assert rt.provider.send_text.await_count == 1


def test_safe_connection_failure_schedules_retry(bot, rt, work):
    result = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    rt.provider.send_text.side_effect = ProviderError('CONNECT_FAILED', retryable=True)
    work(dispatch_one)
    [doc] = rt.pg.sql('SELECT * FROM whatsapp.messages WHERE id = %s', (result['id'],))
    assert doc['status'] == 'retry_wait' and doc['dispatch_started_at'] is None
    assert doc['attempts'] == 1 and doc['due_at'] > now() and doc['lease_owner'] is not None


def test_restart_recovers_leases_without_resending(bot, rt, work):
    started = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    claimed = bot.post('/api/v1/messages/send', json={**PAYLOAD, 'idempotency_key': 'test-2'}).json()
    expired = now() - timedelta(seconds=1)
    rt.pg.sql("UPDATE whatsapp.messages SET status = 'processing', lease_until = %s, dispatch_started_at = now() "
              'WHERE id = %s', (expired, started['id']), fetch=False)
    rt.pg.sql("UPDATE whatsapp.messages SET status = 'processing', lease_until = %s WHERE id = %s",
              (expired, claimed['id']), fetch=False)
    work(dispatch_one)
    assert bot.get('/api/v1/messages/' + started['id']).json()['status'] == 'unknown'
    assert bot.get('/api/v1/messages/' + claimed['id']).json()['status'] == 'sent'
    assert rt.provider.send_text.await_count == 1


def test_license_ended_cancels_queue(bot, rt, work):
    result = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    rt.pg.sql("UPDATE futuranext.tenant_products SET active = false WHERE tenant_id = %s", (rt.pg.tenant_a,), fetch=False)
    try:
        work(dispatch_one)
    finally:
        rt.pg.sql("UPDATE futuranext.tenant_products SET active = true WHERE tenant_id = %s", (rt.pg.tenant_a,), fetch=False)
    [doc] = rt.pg.sql('SELECT status, error_code FROM whatsapp.messages WHERE id = %s', (result['id'],))
    assert (doc['status'], doc['error_code']) == ('cancelled', 'LICENSE_INACTIVE')
    rt.provider.send_text.assert_not_awaited()


def test_webhook_auth_dedup_and_status_precedence(bot, rt, work):
    result = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    work(dispatch_one)
    data = {'keyId': 'provider-message-1', 'status': 'READ'}
    envelope = {'instance': f'wp_{INSTANCE_A}', 'event': 'messages.update', 'apikey': 'NEVER_STORE', 'data': data}
    assert bot.post(HOOK, json=envelope).status_code == 401
    assert bot.post(HOOK, json=envelope, headers={'X-Webhook-Secret': 'errado'}).status_code == 401
    assert bot.post(f'/internal/webhooks/evolution/{INSTANCE_B}', json=envelope,
                    headers={'X-Webhook-Secret': WEBHOOK_SECRET}).status_code == 422
    assert bot.post('/internal/webhooks/evolution/nao-e-uuid', json=envelope,
                    headers={'X-Webhook-Secret': WEBHOOK_SECRET}).status_code == 401
    for _ in range(2):
        assert deliver(bot, 'messages.update', data, apikey='NEVER_STORE').status_code == 200
    events = rt.pg.sql('SELECT * FROM whatsapp.gateway_events')
    assert len(events) == 1 and 'NEVER_STORE' not in str(rt.unseal(events[0]['data']))
    assert deliver(bot, 'messages.update', {'keyId': 'provider-message-1', 'status': 'SERVER_ACK'}).status_code == 200
    work(process_events, NoRegistry())
    assert bot.get('/api/v1/messages/' + result['id']).json()['status'] == 'read'
    assert rt.pg.sql('SELECT count(*) AS total FROM whatsapp.gateway_events WHERE NOT processed')[0]['total'] == 0


def test_status_before_correlation_waits_for_provider_id(bot, rt, work):
    assert deliver(bot, 'messages.update', {'keyId': 'ainda-desconhecido', 'status': 'DELIVERY_ACK'}).status_code == 200
    work(process_events, NoRegistry())
    [event] = rt.pg.sql('SELECT processed, outcome, next_attempt_at FROM whatsapp.gateway_events')
    assert not event['processed'] and event['outcome'] == 'awaiting_correlation' and event['next_attempt_at'] > now()


def test_received_message_and_stop_keyword_create_suppression(bot, rt, work):
    queued = bot.post('/api/v1/messages/send', json=PAYLOAD).json()
    inbound = {'key': {'id': 'inbound-1', 'remoteJid': f'{PHONE}@s.whatsapp.net'}, 'message': {'conversation': 'SAIR'}}
    for _ in range(2):
        assert deliver(bot, 'messages.upsert', inbound).status_code == 200
    work(process_events, NoRegistry())
    received = bot.get('/api/v1/messages', params={'status': 'received'}).json()['items']
    assert [(item['phone'], item['message']) for item in received] == [(PHONE, 'SAIR')]
    assert bot.get('/api/v1/messages/' + queued['id']).json()['status'] == 'cancelled'
    blocked = bot.post('/api/v1/messages/send', json={**PAYLOAD, 'idempotency_key': 'depois-do-sair'})
    assert blocked.status_code == 403 and blocked.json()['error']['code'] == 'OPTED_OUT'
    work(dispatch_one)
    rt.provider.send_text.assert_not_awaited()
    assert rt.pg.sql('SELECT count(*) AS total FROM whatsapp.suppressions')[0]['total'] == 1


def test_connection_events_apply_in_order(bot, rt, work):
    rt.pg.sql("UPDATE whatsapp.instances SET connection_state = 'connecting' WHERE id = %s", (INSTANCE_A,), fetch=False)
    rt.redis.values[f'qr:{INSTANCE_A}'] = 'qr'
    assert deliver(bot, 'connection.update', {'state': 'open', 'wuid': f'{PHONE}@s.whatsapp.net'},
                   date_time='2026-11-18T10:00:00Z').status_code == 200
    work(process_events, NoRegistry())
    [instance] = rt.pg.sql('SELECT * FROM whatsapp.instances WHERE id = %s', (INSTANCE_A,))
    assert instance['connection_state'] == 'connected' and instance['phone_masked'] == '•••• 9999'
    assert PHONE not in str(instance) and f'qr:{INSTANCE_A}' not in rt.redis.values
    assert rt.pg.sql("SELECT action FROM whatsapp.audit_events WHERE action = 'instance.connection.connected'")


def test_lost_stream_signal_keeps_durable_outbox(bot, rt, work):
    rt.redis.xadd = AsyncMock(side_effect=RuntimeError('Redis signal lost'))
    response = bot.post('/api/v1/messages/send', json=PAYLOAD)
    assert response.status_code == 202
    work(dispatch_one)
    assert bot.get('/api/v1/messages/' + response.json()['id']).json()['status'] == 'sent'


def test_key_restricted_to_instance_hides_other_instance_messages(bot, rt):
    rt.pg.sql('''INSERT INTO whatsapp.instances (id, tenant_id, name, provider_ref, connection_state)
                 VALUES ('cccccccc-0000-4000-8000-00000000000c', %s, 'Outra', 'wp_cccccccc-0000-4000-8000-00000000000c',
                 'connected')''', (rt.pg.tenant_a,), fetch=False)
    other = issue_key(rt, rt.pg.tenant_a, ['cccccccc-0000-4000-8000-00000000000c'])
    sent = bot.post('/api/v1/messages/send', headers={'Authorization': 'Bearer ' + other},
                    json={**PAYLOAD, 'instance_id': 'cccccccc-0000-4000-8000-00000000000c'})
    assert sent.status_code == 202
    assert bot.get('/api/v1/messages/' + sent.json()['id']).status_code == 404
    assert bot.get('/api/v1/messages').json()['items'] == []


def test_validation_and_body_limit(bot):
    response = bot.post('/api/v1/messages/send', json={**PAYLOAD, 'unexpected': 'PRIVATE_SECRET'})
    assert response.status_code == 422 and 'PRIVATE_SECRET' not in response.text
    assert bot.post('/api/v1/messages/send', content=b'x' * 1048577).status_code == 413


def test_redis_failure_blocks_authenticated_requests(bot, rt):
    rt.redis.eval = AsyncMock(side_effect=RuntimeError('Unavailable'))
    assert bot.get('/api/v1/instances').status_code == 503


def test_normalize_ignores_groups_and_own_messages():
    rows = [{'key': {'id': '1', 'remoteJid': '123@g.us'}, 'message': {'conversation': 'x'}},
            {'key': {'id': '2', 'fromMe': True, 'remoteJid': f'{PHONE}@s.whatsapp.net'}}]
    assert normalize_event('messages.upsert', rows) == []


def test_adapter_exact_request_and_ambiguous_response():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'key': {'id': 'abc'}})
        async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(handler)) as http:
            await EvolutionProvider(http).send_text('wp_test', PHONE, 'Texto')
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
