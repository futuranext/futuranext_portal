"""API de negócio: acesso sempre limitado ao tenant e à instância autorizada."""
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Request, Response, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from .auth import identity, tenant_filter
from .core import fail, now, new_id
from .providers import ProviderError

router = APIRouter(prefix='/api/v1')
internal = APIRouter(prefix='/internal/webhooks')
PHONE = r'^[1-9][0-9]{7,14}$'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class InstanceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    client_label: str = Field(default='', max_length=100)


class SendText(StrictModel):
    instance_id: str = Field(min_length=1, max_length=64)
    phone: str = Field(pattern=PHONE)
    message: str = Field(min_length=1, max_length=4096)
    idempotency_key: str = Field(pattern=r'^[\x21-\x7e]{1,128}$')


class Consent(StrictModel):
    phone: str = Field(pattern=PHONE)
    source: str = Field(min_length=3, max_length=200)
    evidence_ref: str = Field(min_length=3, max_length=300)
    purpose: Literal['messages'] = 'messages'


class OptOut(StrictModel):
    phone: str = Field(pattern=PHONE)
    reason: str = Field(default='Solicitação do destinatário', max_length=200)


class KeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    instance_ids: list[str] = Field(min_length=1, max_length=50)
    scopes: list[Literal['instances:read', 'messages:read', 'messages:send']] = Field(min_length=1, max_length=3)
    expires_in_days: int = Field(default=90, ge=1, le=365)


def instance_public(doc):
    return {key: doc.get(key) for key in ('id', 'name', 'client_label', 'connection_state',
        'phone_masked', 'last_connected_at', 'last_disconnected_at', 'created_at', 'last_error', 'observed_at')} | {'id': doc['_id']}


async def owned(request, scope, instance_id):
    user = await identity(request, scope)
    rt = request.app.state.rt
    doc = await rt.db.instances.find_one(tenant_filter(user, instance_id))
    if not doc:
        fail(404, 'NOT_FOUND', 'Instância não encontrada.')
    return rt, user, doc


async def cache_qr(rt, instance_id, data):
    qr = data.get('qrcode', data) if isinstance(data, dict) else {}
    image = qr.get('base64', '') if isinstance(qr, dict) else ''
    if isinstance(image, str) and image.startswith('data:image/png;base64,') and len(image) < 400_000:
        await rt.redis.set(f'qr:{instance_id}', rt.seal({'base64': image,
            'expires_at': (now() + timedelta(seconds=60)).isoformat()}), ex=60)


@router.get('/instances', tags=['Instâncias'])
async def instances(request: Request):
    user = await identity(request, 'instances:read')
    docs = await request.app.state.rt.db.instances.find(tenant_filter(user)).sort('created_at', -1).to_list(100)
    return {'items': [instance_public(doc) for doc in docs]}


@router.post('/instances', status_code=201, tags=['Instâncias'])
async def create_instance(body: InstanceCreate, request: Request):
    user = await identity(request, 'instances:write')
    rt = request.app.state.rt
    instance_id, secret = new_id(), secrets.token_urlsafe(32)
    doc = {'_id': instance_id, 'tenant_id': user.tenant_id, 'name': body.name,
        'client_label': body.client_label, 'provider': 'evolution', 'provider_ref': f'wp_{instance_id}',
        'connection_state': 'creating', 'created_at': now(), 'webhook_hash': rt.digest(secret),
        'webhook_secret': rt.seal(secret), 'schema_version': 1}
    try:
        await rt.db.instances.insert_one(doc)
    except DuplicateKeyError:
        fail(409, 'INSTANCE_EXISTS', 'Já existe uma instância com esse nome.')
    await rt.audit(user.tenant_id, user.actor_id, 'instance.create', instance_id)
    try:
        await rt.provider.create(doc['provider_ref'],
            f'{rt.config.webhook_base_url}/internal/webhooks/evolution/{instance_id}', secret)
        await rt.db.instances.update_one({'_id': instance_id, 'tenant_id': user.tenant_id},
                                         {'$set': {'connection_state': 'disconnected'}})
    except ProviderError as exc:
        await rt.db.instances.update_one({'_id': instance_id, 'tenant_id': user.tenant_id},
            {'$set': {'connection_state': 'error', 'last_error': exc.code}})
        fail(503, exc.code, 'Cadastro local preservado, mas o gateway não confirmou a criação. Consulte a instância.')
    doc['connection_state'] = 'disconnected'
    return instance_public(doc)


@router.post('/instances/{instance_id}/connect', status_code=202, tags=['Instâncias'])
async def connect(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:connect', instance_id)
    if not user.session:
        fail(403, 'SESSION_REQUIRED', 'Pareamento exige login no painel.')
    if doc['connection_state'] in ('deleting', 'creating', 'error'):
        fail(409, 'INSTANCE_NOT_READY', 'A instância não está pronta para pareamento.')
    await rt.limit(f'connect:{instance_id}', 3)
    await rt.audit(user.tenant_id, user.actor_id, 'instance.connect', instance_id)
    data = await rt.provider.connect(doc['provider_ref'])
    await cache_qr(rt, instance_id, data)
    return {'status': 'pairing_requested'}


@router.get('/instances/{instance_id}/qr', tags=['Instâncias'])
async def qr(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:connect', instance_id)
    if not user.session:
        fail(403, 'SESSION_REQUIRED', 'Pareamento exige login no painel.')
    value = await rt.redis.get(f'qr:{instance_id}')
    if not value or doc['connection_state'] == 'connected':
        fail(409, 'QR_UNAVAILABLE', 'QR Code ainda não disponível ou já expirou.')
    return rt.unseal(value)


@router.get('/instances/{instance_id}/connection', tags=['Instâncias'])
async def connection(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:read', instance_id)
    if doc['connection_state'] not in ('error', 'creating', 'deleting'):
        state = await rt.provider.connection(doc['provider_ref'])
        update = {'connection_state': state, 'observed_at': now(), 'last_error': None}
        if state != doc['connection_state'] and state in ('connected', 'disconnected'):
            update['last_connected_at' if state == 'connected' else 'last_disconnected_at'] = now()
        await rt.db.instances.update_one({'_id': instance_id, 'tenant_id': user.tenant_id}, {'$set': update})
        doc.update(update)
        if state == 'connected':
            await rt.redis.delete(f'qr:{instance_id}')
    return instance_public(doc)


@router.post('/instances/{instance_id}/disconnect', tags=['Instâncias'])
async def disconnect(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:write', instance_id)
    await rt.provider.disconnect(doc['provider_ref'])
    await rt.redis.delete(f'qr:{instance_id}')
    await rt.db.instances.update_one({'_id': instance_id, 'tenant_id': user.tenant_id},
        {'$set': {'connection_state': 'disconnected', 'last_disconnected_at': now()}})
    await rt.audit(user.tenant_id, user.actor_id, 'instance.disconnect', instance_id)
    return {'status': 'disconnected'}


@router.delete('/instances/{instance_id}', status_code=204, tags=['Instâncias'])
async def delete_instance(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:delete', instance_id)
    await rt.db.instances.update_one({'_id': instance_id, 'tenant_id': user.tenant_id}, {'$set': {'connection_state': 'deleting'}})
    await rt.provider.delete(doc['provider_ref'])
    await rt.db.messages.update_many({'tenant_id': user.tenant_id, 'instance_id': instance_id,
        'status': {'$in': ['queued', 'retry_wait']}}, {'$set': {'status': 'cancelled', 'error_code': 'INSTANCE_DELETED'}})
    await rt.redis.delete(f'qr:{instance_id}')
    await rt.db.instances.delete_one({'_id': instance_id, 'tenant_id': user.tenant_id})
    await rt.audit(user.tenant_id, user.actor_id, 'instance.delete', instance_id)


async def recipient_allowed(rt, tenant_id, phone_hash):
    """O aceite é do titular que pareou o número (portal FuturaNext); opt-out sempre bloqueia."""
    return not await rt.db.suppressions.find_one({'tenant_id': tenant_id, 'phone_hash': phone_hash})


async def consent_allowed(rt, tenant_id, phone_hash):
    query = {'tenant_id': tenant_id, 'phone_hash': phone_hash}
    granted = bool(await rt.db.consent_requests.find_one({**query, 'status': 'confirmed', 'delivery_channel': 'email_zoho'}))
    return granted and not bool(await rt.db.suppressions.find_one(query))


@router.post('/consents', status_code=201, tags=['Consentimento'])
async def consent(body: Consent, request: Request):
    user = await identity(request, 'consents:write')
    fail(410, 'EMAIL_CONSENT_ONLY', 'A autorização deve ser confirmada pelo destinatário via e-mail.')


@router.post('/opt-outs', status_code=201, tags=['Consentimento'])
async def opt_out(body: OptOut, request: Request):
    user = await identity(request, 'consents:write')
    rt = request.app.state.rt
    phone_hash = rt.digest(f'{user.tenant_id}:{body.phone}')
    await rt.db.suppressions.update_one({'tenant_id': user.tenant_id, 'phone_hash': phone_hash},
        {'$set': {'opted_out_at': now(), 'reason': rt.seal(body.reason)}}, upsert=True)
    await rt.db.messages.update_many({'tenant_id': user.tenant_id, 'phone_hash': phone_hash,
        'status': {'$in': ['queued', 'retry_wait']}}, {'$set': {'status': 'cancelled', 'error_code': 'OPTED_OUT'}})
    await rt.audit(user.tenant_id, user.actor_id, 'consent.opt_out')
    return {'status': 'blocked'}


def message_public(rt, doc):
    content = rt.unseal(doc['content']) if doc.get('content') and doc['content_expires_at'] > now() else {'phone': '', 'message': '[Conteúdo expirado]'}
    return {'id': doc['_id'], 'instance_id': doc['instance_id'], 'direction': doc['direction'],
        'status': doc['status'], 'created_at': doc['created_at'], 'error_code': doc.get('error_code'),
        'provider_message_id': doc.get('provider_message_id'), **content}


@router.post('/messages/send', status_code=202, tags=['Mensagens'])
async def send_text(body: SendText, request: Request, response: Response):
    rt, user, instance = await owned(request, 'messages:send', body.instance_id)
    query = {'tenant_id': user.tenant_id, 'instance_id': body.instance_id, 'idempotency_key': body.idempotency_key}
    payload_hash = rt.digest(json.dumps(body.model_dump(exclude={'idempotency_key'}), sort_keys=True, ensure_ascii=False))

    def replay(existing):
        if existing['payload_hash'] != payload_hash:
            fail(409, 'IDEMPOTENCY_CONFLICT', 'Esta chave já foi usada com outro conteúdo.')
        response.status_code = 200
        return {'id': existing['_id'], 'status': existing['status'], 'created_at': existing['created_at']}

    existing = await rt.db.messages.find_one(query)
    if existing:
        return replay(existing)
    phone_hash = rt.digest(f'{user.tenant_id}:{body.phone}')
    if not await recipient_allowed(rt, user.tenant_id, phone_hash):
        fail(403, 'OPTED_OUT', 'Este destinatário pediu para não receber mensagens.')
    if instance['connection_state'] != 'connected':
        fail(409, 'NOT_CONNECTED', 'A instância precisa estar conectada para enviar.')
    await rt.limit(f'enqueue:{user.tenant_id}', 30)
    doc = {'_id': new_id(), **query, 'direction': 'outbound', 'status': 'queued',
        'phone_hash': phone_hash, 'payload_hash': payload_hash, 'content': rt.seal({'phone': body.phone, 'message': body.message}),
        'created_at': now(), 'due_at': now(), 'attempts': 0, 'content_expires_at': now() + timedelta(days=30),
        'expires_at': now() + timedelta(days=90), 'schema_version': 1}
    try:
        await rt.db.messages.insert_one(doc)
    except DuplicateKeyError:
        return replay(await rt.db.messages.find_one(query))
    try:
        await rt.redis.xadd('dispatch', {'id': doc['_id']}, maxlen=10000)
    except Exception:
        pass  # Documento durável será encontrado pelo dispatcher; Redis não é a fonte única.
    await rt.audit(user.tenant_id, user.actor_id, 'message.queued', doc['_id'])
    response.headers['Location'] = f'/api/v1/messages/{doc["_id"]}'
    return {'id': doc['_id'], 'status': 'queued', 'created_at': doc['created_at']}


def messages_filter(user):
    query = {'tenant_id': user.tenant_id, 'expires_at': {'$gt': now()}}
    if user.instance_ids is not None:
        query['instance_id'] = {'$in': user.instance_ids}
    return query


@router.get('/messages', tags=['Mensagens'])
async def messages(request: Request, instance_id: str | None = None, phone: str | None = Query(None, pattern=PHONE),
                   status: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                   cursor: str | None = None, limit: int = Query(25, ge=1, le=100)):
    user = await identity(request, 'messages:read')
    rt = request.app.state.rt
    query = messages_filter(user)
    if instance_id:
        tenant_filter(user, instance_id)
        query['instance_id'] = instance_id
    if phone:
        query['phone_hash'] = rt.digest(f'{user.tenant_id}:{phone}')
    if status:
        query['status'] = status
    if date_from or date_to:
        query['created_at'] = {**({'$gte': date_from} if date_from else {}), **({'$lte': date_to} if date_to else {})}
    if cursor:
        previous = await rt.db.messages.find_one({**messages_filter(user), '_id': cursor})
        if not previous:
            fail(422, 'CURSOR_INVALID', 'Cursor inválido.')
        query['$or'] = [{'created_at': {'$lt': previous['created_at']}},
                        {'created_at': previous['created_at'], '_id': {'$lt': previous['_id']}}]
    docs = await rt.db.messages.find(query).sort([('created_at', -1), ('_id', -1)]).to_list(limit + 1)
    return {'items': [message_public(rt, d) for d in docs[:limit]], 'next_cursor': docs[limit-1]['_id'] if len(docs) > limit else None}


@router.get('/messages/{message_id}', tags=['Mensagens'])
async def message_status(message_id: str, request: Request):
    user = await identity(request, 'messages:read')
    rt = request.app.state.rt
    doc = await rt.db.messages.find_one({**messages_filter(user), '_id': message_id})
    if not doc:
        fail(404, 'NOT_FOUND', 'Mensagem não encontrada.')
    return message_public(rt, doc)


@router.post('/api-keys', status_code=201, tags=['API Keys'])
async def create_key(body: KeyCreate, request: Request):
    user = await identity(request, 'keys:write')
    rt = request.app.state.rt
    ids = list(set(body.instance_ids))
    if await rt.db.instances.count_documents({'tenant_id': user.tenant_id, '_id': {'$in': ids}}) != len(ids):
        fail(404, 'NOT_FOUND', 'Uma das instâncias não foi encontrada.')
    secret = 'wp_' + secrets.token_urlsafe(32)
    doc = {'_id': new_id(), 'tenant_id': user.tenant_id, 'name': body.name, 'prefix': secret[:10],
        'secret_hash': rt.digest(secret), 'scopes': body.scopes, 'instance_ids': ids,
        'created_at': now(), 'expires_at': now() + timedelta(days=body.expires_in_days), 'revoked_at': None}
    await rt.db.api_keys.insert_one(doc)
    await rt.audit(user.tenant_id, user.actor_id, 'key.create', doc['_id'])
    return {'id': doc['_id'], 'secret': secret, 'expires_at': doc['expires_at']}


@router.get('/api-keys', tags=['API Keys'])
async def keys(request: Request):
    user = await identity(request, 'keys:read')
    docs = await request.app.state.rt.db.api_keys.find({'tenant_id': user.tenant_id}, {'secret_hash': 0}).sort('created_at', -1).to_list(100)
    return {'items': [{key: value for key, value in d.items() if key not in ('_id', 'tenant_id')} | {'id': d['_id']} for d in docs]}


@router.delete('/api-keys/{key_id}', status_code=204, tags=['API Keys'])
async def revoke(key_id: str, request: Request):
    user = await identity(request, 'keys:write')
    rt = request.app.state.rt
    result = await rt.db.api_keys.update_one({'_id': key_id, 'tenant_id': user.tenant_id}, {'$set': {'revoked_at': now()}})
    if not result.matched_count:
        fail(404, 'NOT_FOUND', 'Chave não encontrada.')
    await rt.audit(user.tenant_id, user.actor_id, 'key.revoke', key_id)


@router.get('/dashboard', tags=['Painel'])
async def dashboard(request: Request):
    user = await identity(request, 'dashboard:read')
    rt = request.app.state.rt
    query = {'tenant_id': user.tenant_id}
    count = await rt.db.instances.count_documents(query)
    connected = await rt.db.instances.count_documents({**query, 'connection_state': 'connected'})
    stats = {}
    for name, statuses in {'sent': ['sent', 'delivered', 'read'], 'received': ['received'],
                            'failed': ['failed', 'unknown'], 'queued': ['queued', 'processing', 'retry_wait']}.items():
        stats[name] = await rt.db.messages.count_documents({**query, 'status': {'$in': statuses}, 'expires_at': {'$gt': now()}})
    events = await rt.db.audit_events.find(query).sort('created_at', -1).to_list(10)
    heartbeat = await rt.redis.get('worker:heartbeat')
    return {'instances': count, 'connected': connected, 'disconnected': count-connected,
        **stats, 'worker_online': bool(heartbeat),
        'events': [{'action': e['action'], 'created_at': e['created_at']} for e in events]}


@internal.post('/evolution/{instance_id}', include_in_schema=False)
async def webhook(instance_id: str, request: Request):
    rt = request.app.state.rt
    doc = await rt.db.instances.find_one({'_id': instance_id})
    supplied = request.headers.get('x-webhook-secret', '')
    if not doc or not supplied or not hmac.compare_digest(rt.digest(supplied), doc['webhook_hash']):
        fail(401, 'INVALID_WEBHOOK', 'Webhook não autorizado.')
    try:
        body = await request.json()
    except ValueError:
        fail(422, 'INVALID_WEBHOOK', 'JSON inválido.')
    if not isinstance(body, dict) or body.get('instance') != doc['provider_ref']:
        fail(422, 'INVALID_WEBHOOK', 'Instância divergente.')
    event = str(body.get('event', '')).lower().replace('_', '.')
    data = body.get('data')
    if event == 'qrcode.updated':
        await cache_qr(rt, instance_id, data)
        return {'status': 'accepted'}
    if event not in ('connection.update', 'messages.upsert', 'messages.update'):
        return {'status': 'ignored'}
    if not isinstance(data, (dict, list)):
        fail(422, 'INVALID_WEBHOOK', 'Dados inválidos.')
    # Nunca persistir envelope com apikey, URLs ou outros segredos do gateway.
    from .events import normalize_event
    normalized = normalize_event(event, data)
    for item in normalized:
        if event == 'connection.update':
            # Cada reconexão precisa ser aplicada, mesmo com estado igual a uma conexão antiga.
            item['occurrence'] = str(body.get('date_time') or new_id())[:100]
        event_id = rt.digest(json.dumps([instance_id, event, item], sort_keys=True))
        await rt.db.gateway_events.update_one({'_id': event_id}, {'$setOnInsert': {
            'tenant_id': doc['tenant_id'], 'instance_id': instance_id, 'type': event, 'data': rt.seal(item),
            'created_at': now(), 'processed': False, 'expires_at': now() + timedelta(days=14)}}, upsert=True)
    return {'status': 'accepted'}
