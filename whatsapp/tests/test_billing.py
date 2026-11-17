import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from pydantic import SecretStr
from backend.app import asaas, billing, email_service
from backend.app.core import now


@pytest.fixture
def enabled(rt, monkeypatch):
    rt.config.asaas_enabled = True
    rt.config.asaas_api_key = SecretStr('synthetic')
    rt.config.asaas_webhook_token = SecretStr('synthetic-webhook-token-32-characters')
    monkeypatch.setattr(email_service, 'email_ready', lambda config: True)
    mock = AsyncMock(side_effect=[{'id': 'cus_test'}, {'id': 'sub_test'}])
    monkeypatch.setattr(asaas, 'call', mock)
    return mock


BODY = {'name': 'Cliente sintético', 'cpf_cnpj': '00000000000', 'accepted': True, 'price_cents': 6000}


def test_monthly_contract_and_duplicate(admin, rt, enabled):
    assert admin.post('/api/v1/billing/subscribe', json=BODY).status_code == 202
    payload = enabled.call_args_list[1].args[3]
    assert payload['cycle'] == 'MONTHLY' and payload['value'] == 60 and payload['billingType'] == 'UNDEFINED'
    assert admin.post('/api/v1/billing/subscribe', json=BODY).status_code == 409
    assert enabled.await_count == 2
    result = admin.get('/api/v1/billing').json()
    assert result['contracted_price_cents'] == 6000
    assert 'recipient' not in result and 'cpf_cnpj' not in result


def test_price_and_isolation(admin, rt):
    assert admin.post('/api/v1/admin/billing/price', json={'price_cents': 7000}).status_code == 403
    asyncio.run(rt.db.users.update_one({'_id': 'user-a'}, {'$set': {'role': 'global_admin'}}))
    assert admin.post('/api/v1/admin/billing/price', json={'price_cents': 7000}).status_code == 200
    assert admin.post('/api/v1/admin/billing/price', json={'price_cents': 60.5}).status_code == 422
    asyncio.run(rt.db.billing_payments.insert_one({'_id': 'private', 'tenant_id': 'tenant-b', 'environment': 'sandbox', 'invoice_url': 'private'}))
    assert admin.get('/api/v1/billing').json()['payments'] == []
    asyncio.run(rt.db.users.update_one({'_id': 'user-a'}, {'$set': {'role': 'tenant_admin'}}))
    assert admin.get('/api/v1/billing', headers={'X-Tenant-ID': 'tenant-b'}).status_code == 403


def test_timeout_never_repeats_mutation(admin, enabled):
    enabled.side_effect = asaas.AsaasError()
    assert admin.post('/api/v1/billing/subscribe', json=BODY).status_code == 503
    assert admin.get('/api/v1/billing').json()['status'] == 'review'
    assert admin.post('/api/v1/billing/subscribe', json=BODY).status_code == 409
    assert enabled.await_count == 1


def test_explicit_acceptance_current_price_and_disabled(admin, enabled, rt):
    assert admin.post('/api/v1/billing/subscribe', json={**BODY, 'accepted': False}).status_code == 422
    assert admin.post('/api/v1/billing/subscribe', json={**BODY, 'price_cents': 5000}).status_code == 409
    rt.config.asaas_enabled = False
    assert admin.post('/api/v1/billing/subscribe', json=BODY).status_code == 503
    enabled.assert_not_awaited()


def test_webhook_is_authenticated_deduplicated_signal(admin, rt, enabled):
    admin.post('/api/v1/billing/subscribe', json=BODY)
    payload = {'id': 'evt_test', 'payment': {'subscription': 'sub_test', 'status': 'RECEIVED', 'value': 1}}
    assert admin.post('/api/v1/billing/webhook', json=payload).status_code == 401
    headers = {'asaas-access-token': rt.config.asaas_webhook_token.get_secret_value()}
    for _ in range(2):
        assert admin.post('/api/v1/billing/webhook', json=payload, headers=headers).status_code == 200
    assert asyncio.run(rt.db.billing_events.count_documents({})) == 1
    assert asyncio.run(rt.db.billing_payments.count_documents({})) == 0


def test_reminder_once_and_only_pending(admin, rt, enabled, monkeypatch):
    admin.post('/api/v1/billing/subscribe', json=BODY)
    doc = asyncio.run(rt.db.billing_accounts.find_one({'tenant_id': 'tenant-a'}))
    due = (now() + timedelta(days=2)).date().isoformat()
    payment = {'id': 'pay_test', 'subscription': 'sub_test', 'customer': 'cus_test', 'status': 'PENDING',
               'value': 60, 'dueDate': due, 'invoiceUrl': 'https://sandbox.asaas.com/i/test'}
    enabled.side_effect = [{'status': 'ACTIVE'}, {'data': [payment], 'hasMore': False}, payment]
    mail = AsyncMock(return_value='unknown')
    monkeypatch.setattr(email_service, 'send_email', mail)
    asyncio.run(billing.reconcile(rt, doc))
    asyncio.run(billing.reminders(rt, doc))
    asyncio.run(billing.reminders(rt, doc))
    assert mail.await_count == 1
    assert asyncio.run(rt.db.billing_payments.find_one({}))['reminder_status'] == 'unknown'


def test_invoice_link_blocks_arbitrary_destinations():
    for url in ['javascript:alert(1)', 'https://evil.test/i/1', 'https://asaas.com.evil.test', 'http://asaas.com', 'https://user@asaas.com']:
        assert asaas.invoice_url(url) is None


def test_cancel_once(admin, enabled):
    admin.post('/api/v1/billing/subscribe', json=BODY)
    enabled.side_effect = [{'deleted': True}]
    assert admin.post('/api/v1/billing/cancel').status_code == 200
    assert admin.post('/api/v1/billing/cancel').status_code == 409
