import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from pydantic import SecretStr
from backend.app.core import now
from backend.app import email_service

@pytest.fixture(autouse=True)
def mail(rt, monkeypatch):
    rt.config.consent_base_url = 'https://panel.example.com'
    rt.config.zoho_api_key = SecretStr('synthetic-only')
    sender = AsyncMock(return_value='accepted')
    monkeypatch.setattr(email_service, 'send_email', sender)
    return sender

def invite(client):
    response = client.post('/api/v1/consent-requests', json={'phone': '5511999999999', 'email': 'customer@example.test', 'purpose': 'Atualizações das cotações solicitadas.'})
    assert response.status_code == 201, response.text
    item = response.json()
    assert 'url' not in item
    rt = client.app.state.rt
    doc = asyncio.run(rt.db.consent_requests.find_one({'_id': item['id']}))
    return {**item, '_test_token': rt.unseal(doc['token_encrypted'])}

def accept(client, item, **kwargs):
    return client.post('/api/v1/public/consent/confirm', headers={'Origin': 'https://panel.example.com'}, json={'token': item['_test_token'], 'accepted': True, 'statement_version': '1', **kwargs})

def test_open_not_consent_and_confirmation_idempotent(admin, rt, mail):
    item = invite(admin)
    mail.assert_awaited_once()
    doc = asyncio.run(rt.db.consent_requests.find_one({'_id': item['id']}))
    assert item['_test_token'] not in str(doc)
    details = admin.post('/api/v1/public/consent/details', headers={'Origin': 'https://panel.example.com'}, json={'token': item['_test_token']})
    assert details.json()['status'] == 'pending'
    from backend.app.api import consent_allowed
    assert not asyncio.run(consent_allowed(rt, 'tenant-a', doc['phone_hash']))
    for values in ({'accepted': False}, {'accepted': 1}, {'statement_version': '2'}):
        assert accept(admin, item, **values).status_code == 422
    first = accept(admin, item)
    assert first.status_code == 200 and accept(admin, item).json() == first.json()
    assert asyncio.run(consent_allowed(rt, 'tenant-a', doc['phone_hash']))
    saved = asyncio.run(rt.db.consent_requests.find_one({'_id': item['id']}))
    assert saved['accepted'] is True and 'token_encrypted' not in saved
    assert rt.unseal(saved['content'])['statement'] == details.json()['statement']

def test_manual_and_legacy_consents_do_not_allow_sending(admin, rt):
    assert admin.post('/api/v1/consents', json={'phone': '5511999999999', 'source': 'Manual', 'evidence_ref': 'Teste'}).status_code == 410
    query = {'tenant_id': 'tenant-a', 'phone_hash': rt.digest('tenant-a:5511999999999')}
    asyncio.run(rt.db.consents.insert_one(query.copy()))
    asyncio.run(rt.db.consent_requests.insert_one({**query, 'status': 'confirmed'}))
    from backend.app.api import consent_allowed
    assert not asyncio.run(consent_allowed(rt, 'tenant-a', query['phone_hash']))

def test_expiry_cancellation_and_optout(admin, rt):
    expired = invite(admin)
    asyncio.run(rt.db.consent_requests.update_one({'_id': expired['id']}, {'$set': {'expires_at': now() - timedelta(seconds=1)}}))
    assert accept(admin, expired).status_code == 410
    cancelled = invite(admin)
    assert admin.delete('/api/v1/consent-requests/' + cancelled['id']).status_code == 204
    assert accept(admin, cancelled).status_code == 404
    blocked = invite(admin)
    admin.post('/api/v1/opt-outs', json={'phone': '5511999999999'})
    assert accept(admin, blocked).status_code == 409

def test_anonymous_confirmation_requires_secret_and_origin(admin):
    item = invite(admin)
    admin.post('/api/v1/auth/logout')
    assert admin.get('/api/v1/consent-requests').status_code == 401
    assert admin.post('/api/v1/public/consent/confirm', json={'token': item['_test_token'], 'accepted': True, 'statement_version': '1'}).status_code == 403
    assert accept(admin, item).status_code == 200

def test_other_tenant_cannot_list_cancel_or_send(admin, rt):
    item = invite(admin)
    asyncio.run(rt.db.consent_requests.update_one({'_id': item['id']}, {'$set': {'tenant_id': 'tenant-b'}}))
    assert admin.get('/api/v1/consent-requests').json()['items'] == []
    assert admin.delete('/api/v1/consent-requests/' + item['id']).status_code == 409
    assert admin.post(f"/api/v1/consent-requests/{item['id']}/email").status_code == 409

def test_email_ambiguous_result_has_no_retry(admin, mail):
    mail.return_value = 'unknown'
    item = invite(admin)
    assert item['email_status'] == 'unknown'
    assert admin.post(f"/api/v1/consent-requests/{item['id']}/email").status_code == 409
    mail.assert_awaited_once()

def test_no_email_configuration_cannot_generate_operator_link(admin, rt, mail):
    rt.config.consent_base_url = 'http://localhost:3000'
    assert admin.get('/api/v1/consent-requests').json()['email_ready'] is False
    assert admin.post('/api/v1/consent-requests', json={'phone': '5511999999999', 'email': 'customer@example.test', 'purpose': 'Atualizações solicitadas.'}).status_code == 503
    mail.assert_not_awaited()
