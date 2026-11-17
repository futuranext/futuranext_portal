"""Assinaturas mensais, conciliação por consulta e lembrete Zoho de tentativa única."""
import hmac
import re
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo.errors import DuplicateKeyError
from .auth import identity
from .core import now, new_id, fail
from . import asaas, email_service

router = APIRouter(prefix='/api/v1', tags=['Assinaturas'])


class Price(BaseModel):
    model_config = ConfigDict(extra='forbid')
    price_cents: int = Field(strict=True, ge=500, le=1000000)


class Subscribe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=3, max_length=120)
    cpf_cnpj: str = Field(pattern=r'^(?:[0-9]{11}|[0-9]{14})$')
    accepted: StrictBool
    price_cents: int = Field(strict=True, ge=500, le=1000000)


async def member(request):
    user = await identity(request)
    if not user.session or '*' not in user.scopes:
        fail(403, 'FORBIDDEN', 'Acesso exclusivo do administrador da conta.')
    return user


def account_query(rt, tenant):
    return {'tenant_id': tenant, 'environment': rt.config.asaas_environment}


@router.get('/billing')
async def overview(request: Request):
    user = await member(request)
    rt = request.app.state.rt
    plan = await rt.db.billing_settings.find_one({'_id': 'monthly'})
    query = account_query(rt, user.tenant_id)
    account = await rt.db.billing_accounts.find_one(query)
    payments = await rt.db.billing_payments.find(query, {'_id': 0, 'provider_id': 1,
        'status': 1, 'due_date': 1, 'value_cents': 1, 'invoice_url': 1, 'reminder_status': 1}).sort('due_date', -1).to_list(100)
    return {'price_cents': plan['price_cents'], 'environment': rt.config.asaas_environment,
        'configured': asaas.ready(rt.config), 'email_ready': email_service.email_ready(rt.config),
        'status': account['status'] if account else 'none',
        'contracted_price_cents': account.get('price_cents') if account else None,
        'payments': payments}


@router.post('/admin/billing/price')
async def set_price(body: Price, request: Request):
    user = await member(request)
    if not user.global_admin:
        fail(403, 'FORBIDDEN', 'Acesso exclusivo do administrador da plataforma.')
    rt = request.app.state.rt
    await rt.db.billing_settings.update_one({'_id': 'monthly'}, {'$set': {'price_cents': body.price_cents, 'updated_at': now()}})
    await rt.audit(user.owner_tenant, user.actor_id, 'billing.price_changed')
    return {'price_cents': body.price_cents}


@router.post('/billing/subscribe', status_code=202)
async def subscribe(body: Subscribe, request: Request):
    user = await member(request)
    rt = request.app.state.rt
    if not asaas.ready(rt.config):
        fail(503, 'BILLING_NOT_CONFIGURED', 'O Asaas ainda não está habilitado.')
    email_service.require_email(rt.config)
    if not body.accepted:
        fail(422, 'ACCEPT_REQUIRED', 'Confirme a contratação mensal.')
    plan = await rt.db.billing_settings.find_one({'_id': 'monthly'})
    if body.price_cents != plan['price_cents']:
        fail(409, 'PRICE_CHANGED', 'O preço mudou. Atualize a página antes de contratar.')
    # Destinatário deriva da conta, nunca de um e-mail arbitrário do formulário.
    owner = await rt.db.users.find_one({'tenant_id': user.tenant_id, 'disabled': False,
        'role': {'$in': ['tenant_admin', 'global_admin']}})
    if not owner:
        fail(409, 'ACCOUNT_UNAVAILABLE', 'Conta indisponível para cobrança.')
    query = account_query(rt, user.tenant_id)
    doc = {**query, '_id': new_id(), 'status': 'creating', 'price_cents': plan['price_cents'],
        'created_at': now(), 'recipient': rt.seal(owner['email'])}
    try:
        await rt.db.billing_accounts.insert_one(doc)
    except DuplicateKeyError:
        fail(409, 'SUBSCRIPTION_EXISTS', 'Já existe uma contratação ou operação em análise. Não repita a cobrança; consulte o administrador.')
    try:
        customer = await asaas.call(rt.config, 'POST', '/customers', {'name': body.name,
            'cpfCnpj': body.cpf_cnpj, 'email': owner['email'], 'notificationDisabled': True,
            'externalReference': doc['_id']})
        customer_id = customer['id']
        await rt.db.billing_accounts.update_one({**query, '_id': doc['_id']}, {'$set': {'customer_id': customer_id}})
        due = (now().astimezone(ZoneInfo('America/Sao_Paulo')).date() + timedelta(days=3)).isoformat()
        subscription = await asaas.call(rt.config, 'POST', '/subscriptions', {'customer': customer_id,
            'billingType': 'UNDEFINED', 'value': plan['price_cents'] / 100,
            'cycle': 'MONTHLY', 'nextDueDate': due, 'externalReference': doc['_id'],
            'description': 'Central WhatsApp — mensalidade', 'fine': {'value': 0}, 'interest': {'value': 0}})
        sid = subscription['id']
        if not re.fullmatch(r'sub_[A-Za-z0-9_-]+', sid):
            raise ValueError()
        await rt.db.billing_accounts.update_one({**query, '_id': doc['_id']},
            {'$set': {'subscription_id': sid, 'status': 'active', 'sync_due': now()}})
    except (asaas.AsaasError, KeyError, ValueError):
        await rt.db.billing_accounts.update_one({**query, '_id': doc['_id']}, {'$set': {'status': 'review'}})
        fail(503, 'BILLING_REVIEW', 'Não foi possível confirmar a contratação. O administrador deve conferir o Asaas antes de qualquer nova tentativa.')
    await rt.audit(user.tenant_id, user.actor_id, 'billing.subscribed', doc['_id'])
    return {'status': 'active'}


@router.post('/billing/cancel')
async def cancel(request: Request):
    user = await member(request)
    rt = request.app.state.rt
    query = account_query(rt, user.tenant_id)
    doc = await rt.db.billing_accounts.find_one_and_update({**query, 'status': 'active'}, {'$set': {'status': 'cancelling'}})
    if not doc:
        fail(409, 'BILLING_REVIEW', 'Não há assinatura ativa disponível para cancelar.')
    try:
        await asaas.call(rt.config, 'DELETE', '/subscriptions/' + doc['subscription_id'])
    except asaas.AsaasError:
        await rt.db.billing_accounts.update_one(query, {'$set': {'status': 'review'}})
        fail(503, 'BILLING_REVIEW', 'Cancelamento incerto. Consulte o administrador antes de repetir.')
    await rt.db.billing_accounts.update_one(query, {'$set': {'status': 'cancelled'}})
    await rt.db.billing_payments.update_many({**query, 'status': {'$in': ['PENDING', 'OVERDUE']}}, {'$set': {'status': 'DELETED'}})
    await rt.audit(user.tenant_id, user.actor_id, 'billing.cancelled', doc['_id'])
    return {'status': 'cancelled'}


@router.post('/billing/webhook')
async def webhook(request: Request):
    rt = request.app.state.rt
    secret = rt.config.asaas_webhook_token.get_secret_value()
    if not asaas.ready(rt.config) or len(secret) < 32 or not hmac.compare_digest(secret, request.headers.get('asaas-access-token', '')):
        fail(401, 'UNAUTHORIZED', 'Notificação inválida.')
    data = await request.json()
    event_id = data.get('id') if isinstance(data, dict) else None
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
        fail(422, 'INVALID_EVENT', 'Evento inválido.')
    obj = data.get('payment') or data.get('subscription') or {}
    if not isinstance(obj, dict):
        fail(422, 'INVALID_EVENT', 'Evento inválido.')
    sid = obj.get('subscription') if data.get('payment') else obj.get('id')
    if not isinstance(sid, str):
        return {'received': True}
    # Evento só sinaliza consulta canônica; nunca aplica valor/status recebido no corpo.
    doc = await rt.db.billing_accounts.find_one({'subscription_id': sid, 'environment': rt.config.asaas_environment})
    if not doc:
        return {'received': True}
    eid = rt.config.asaas_environment + ':' + event_id
    if await rt.db.billing_events.find_one({'_id': eid}):
        return {'received': True}
    await rt.db.billing_accounts.update_one({'_id': doc['_id'], 'tenant_id': doc['tenant_id']}, {'$set': {'sync_due': now()}})
    await rt.db.billing_events.update_one({'_id': eid}, {'$setOnInsert': {'tenant_id': doc['tenant_id'],
        'expires_at': now() + timedelta(days=30)}}, upsert=True)
    return {'received': True}


async def reconcile(rt, doc):
    query = {**account_query(rt, doc['tenant_id']), '_id': doc['_id']}
    # Lease antes da rede limita concorrência e evita loop agressivo em falhas.
    lease = now() + timedelta(minutes=15)
    claimed = await rt.db.billing_accounts.update_one({**query, 'sync_due': {'$lte': now()}},
        {'$set': {'sync_due': lease}})
    if not claimed.modified_count:
        return
    try:
        subscription = await asaas.call(rt.config, 'GET', '/subscriptions/' + doc['subscription_id'])
    except asaas.AsaasError as exc:
        if exc.status != 404:
            raise
        subscription = {'deleted': True}
    if subscription.get('deleted') or subscription.get('status') == 'INACTIVE':
        status = 'cancelled' if subscription.get('deleted') else 'INACTIVE'
        await rt.db.billing_accounts.update_one(query, {'$set': {'status': status, 'sync_due': now() + timedelta(hours=24)}})
        if status == 'cancelled':
            await rt.db.billing_payments.update_many({**account_query(rt, doc['tenant_id']),
                'status': {'$in': ['PENDING', 'OVERDUE']}}, {'$set': {'status': 'DELETED'}})
        doc['status'] = status
        return
    if doc['status'] == 'INACTIVE' and subscription.get('status') == 'ACTIVE':
        await rt.db.billing_accounts.update_one(query, {'$set': {'status': 'active'}})
        doc['status'] = 'active'
    offset = 0
    while True:
        page = await asaas.call(rt.config, 'GET', f"/subscriptions/{doc['subscription_id']}/payments?limit=100&offset={offset}")
        for payment in page.get('data', []):
            if payment.get('subscription') != doc['subscription_id'] or payment.get('customer') != doc['customer_id']:
                continue
            pid = payment['id']
            cents = int(Decimal(str(payment['value'])) * 100)
            await rt.db.billing_payments.update_one({'_id': doc['environment'] + ':' + pid, 'tenant_id': doc['tenant_id']},
                {'$set': {**account_query(rt, doc['tenant_id']), 'provider_id': pid,
                    'status': payment['status'], 'due_date': payment['dueDate'], 'value_cents': cents,
                    'invoice_url': asaas.invoice_url(payment.get('invoiceUrl')), 'updated_at': now()}}, upsert=True)
        if not page.get('hasMore'):
            break
        offset += 100
        if offset >= 10000:
            raise asaas.AsaasError()
    await rt.db.billing_accounts.update_one({**query, 'sync_due': lease}, {'$set': {'sync_due': now() + timedelta(hours=24)}})


async def reminders(rt, doc):
    if not email_service.email_ready(rt.config) or doc['status'] != 'active':
        return
    today = now().astimezone(ZoneInfo('America/Sao_Paulo')).date()
    query = {**account_query(rt, doc['tenant_id']), 'status': 'PENDING',
        'due_date': {'$gte': today.isoformat(), '$lte': (today + timedelta(days=3)).isoformat()},
        'reminder_status': {'$exists': False}, 'invoice_url': {'$ne': None}}
    for payment in await rt.db.billing_payments.find(query).to_list(100):
        # Confere status imediatamente antes de avisar; sem confiar em cache antigo.
        fresh = await asaas.call(rt.config, 'GET', '/payments/' + payment['provider_id'])
        if fresh.get('status') != 'PENDING' or fresh.get('subscription') != doc['subscription_id'] or fresh.get('dueDate') != payment['due_date'] or int(Decimal(str(fresh.get('value', 0))) * 100) != payment['value_cents']:
            continue
        claimed = await rt.db.billing_payments.update_one({'_id': payment['_id'], 'tenant_id': doc['tenant_id'],
            'reminder_status': {'$exists': False}}, {'$set': {'reminder_status': 'sending', 'reminder_at': now()}})
        if not claimed.modified_count:
            continue
        message = f"Sua mensalidade de R$ {payment['value_cents'] / 100:.2f} vence em {payment['due_date']}. Se já pagou, desconsidere."
        url = payment['invoice_url']
        status = await email_service.send_email(rt.config, recipient=rt.unseal(doc['recipient']),
            subject='Lembrete de vencimento — Central WhatsApp',
            html=email_service.link_message('Sua mensalidade está próxima', message, url, 'Consultar cobrança', 'Pagamento pelo Asaas.'),
            text=message + '\n' + url, reference='billing-' + payment['_id'])
        await rt.db.billing_payments.update_one({'_id': payment['_id'], 'tenant_id': doc['tenant_id']}, {'$set': {'reminder_status': status}})


async def billing_tick(rt):
    if not asaas.ready(rt.config):
        return
    query = {'environment': rt.config.asaas_environment, 'status': {'$in': ['active', 'INACTIVE']}}
    async for doc in rt.db.billing_accounts.find(query):
        try:
            if doc.get('sync_due', now()) <= now():
                await reconcile(rt, doc)
            await reminders(rt, doc)
        except (asaas.AsaasError, ValueError, KeyError):
            # Apenas leitura pode repetir; mutações e e-mails incertos não repetem.
            continue
