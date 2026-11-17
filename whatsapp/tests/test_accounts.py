import asyncio
import re
from datetime import timedelta
from unittest.mock import AsyncMock
import httpx
import pytest
from pydantic import SecretStr
from backend.app import email_service
from backend.app.core import now

@pytest.fixture
def mail(rt, monkeypatch):
    rt.config.consent_base_url = 'https://panel.example.com'
    rt.config.zoho_api_key = SecretStr('synthetic')
    mock = AsyncMock(return_value='accepted')
    monkeypatch.setattr(email_service, 'send_email', mock)
    return mock

def mail_token(mail):
    return re.search(r'#([A-Za-z0-9_-]{43})', mail.call_args.kwargs['text']).group(1)

def test_verified_registration_has_private_tenant_and_cannot_escalate(client, rt, mail):
    payload = {'email': 'new@example.test', 'company': 'Empresa Nova'}
    assert client.post('/api/v1/auth/register', json={**payload, 'role': 'global_admin'}).status_code == 422
    assert client.post('/api/v1/auth/register', json=payload).status_code == 202
    assert not asyncio.run(rt.db.users.find_one({'email': payload['email']}))
    token = mail_token(mail)
    response = client.post('/api/v1/auth/complete-registration', json={'token': token, 'password': 'New-password-123!'})
    assert response.status_code == 200
    account = asyncio.run(rt.db.users.find_one({'email': payload['email']}))
    assert account['role'] == 'tenant_admin' and account['tenant_id'] not in ('tenant-a', 'tenant-b')
    assert token not in str(asyncio.run(rt.db.account_requests.find_one({'_id': account['_id']})))
    assert client.post('/api/v1/auth/complete-registration', json={'token': token, 'password': 'Other-password-123!'}).status_code == 400
    assert client.post('/api/v1/auth/login', json={'email': payload['email'], 'password': 'New-password-123!'}).status_code == 200
    assert client.get('/api/v1/instances').json()['items'] == []
    assert client.get('/api/v1/instances/instance-a/connection').status_code == 404
    assert client.get('/api/v1/admin/accounts').status_code == 403
    assert client.get('/api/v1/instances', headers={'X-Tenant-ID': 'tenant-a'}).status_code == 403

def test_reset_is_single_use_and_revokes_old_sessions(admin, rt, mail):
    old_cookies = dict(admin.cookies)
    assert admin.post('/api/v1/auth/forgot-password', json={'email': 'admin@example.test'}).status_code == 202
    token = mail_token(mail)
    assert admin.post('/api/v1/auth/reset-password', json={'token': token, 'password': 'New-password-456!'}).status_code == 200
    admin.cookies.update(old_cookies)
    assert admin.get('/api/v1/instances').status_code == 401
    assert admin.post('/api/v1/auth/reset-password', json={'token': token, 'password': 'Other-password-456!'}).status_code == 400
    assert admin.post('/api/v1/auth/login', json={'email': 'admin@example.test', 'password': 'Test-password-123!'}).status_code == 401
    assert admin.post('/api/v1/auth/login', json={'email': 'admin@example.test', 'password': 'New-password-456!'}).status_code == 200

def test_reset_unknown_email_same_response_and_expired_token(client, rt, mail):
    unknown = client.post('/api/v1/auth/forgot-password', json={'email': 'unknown@example.test'})
    mail.assert_not_awaited()
    known = client.post('/api/v1/auth/forgot-password', json={'email': 'admin@example.test'})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()
    token = mail_token(mail)
    asyncio.run(rt.db.users.update_one({'_id': 'user-a'}, {'$set': {'reset_expires_at': now() - timedelta(seconds=1)}}))
    assert client.post('/api/v1/auth/reset-password', json={'token': token, 'password': 'New-password-456!'}).status_code == 400

def test_global_admin_explicit_scope_and_audit(admin, rt):
    asyncio.run(rt.db.tenants.insert_one({'_id': 'tenant-b', 'name': 'Outra empresa'}))
    assert admin.get('/api/v1/admin/accounts').status_code == 403
    asyncio.run(rt.db.users.update_one({'_id': 'user-a'}, {'$set': {'role': 'global_admin'}}))
    result = admin.get('/api/v1/admin/accounts')
    assert result.status_code == 200 and len(result.json()['items']) == 2
    assert 'password_hash' not in result.text
    scoped = admin.get('/api/v1/instances', headers={'X-Tenant-ID': 'tenant-b'})
    assert [item['id'] for item in scoped.json()['items']] == ['instance-b']
    me = admin.get('/api/v1/auth/me', headers={'X-Tenant-ID': 'tenant-b'}).json()
    assert me['company'] == 'Outra empresa' and me['owner_tenant_id'] == 'tenant-a'
    assert asyncio.run(rt.db.audit_events.count_documents({'tenant_id': 'tenant-b', 'action': 'admin.account_access'})) > 0

@pytest.mark.parametrize('prefix', ['', 'Zoho-enczapikey ', 'zoho-enczapikey '])
def test_email_adapter_normalizes_key_and_disables_tracking(rt, monkeypatch, prefix):
    rt.config.consent_base_url = 'https://panel.example.com'
    rt.config.zoho_api_key = SecretStr(prefix + 'synthetic')
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers['Authorization'] == 'Zoho-enczapikey synthetic'
        return httpx.Response(200, json={'status': 'success'})
    original = httpx.AsyncClient
    monkeypatch.setattr(email_service.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    status = asyncio.run(email_service.send_email(rt.config, recipient='test@example.test', subject='Teste', html='<p>Teste</p>', text='Teste', reference='test-1'))
    assert status == 'accepted' and len(seen) == 1
    assert b'"track_clicks":false' in seen[0].content

def test_email_adapter_timeout_unknown_without_retry(rt, monkeypatch):
    rt.config.consent_base_url = 'https://panel.example.com'
    rt.config.zoho_api_key = SecretStr('synthetic')
    seen = []
    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout('timeout')
    original = httpx.AsyncClient
    monkeypatch.setattr(email_service.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(email_service.send_email(rt.config, recipient='test@example.test', subject='Teste', html='Teste', text='Teste', reference='test')) == 'unknown'
    assert len(seen) == 1
