"""API interna usada somente pela API do portal FuturaNext.

A rota não é publicada pelo proxy. O portal autentica o usuário, confere a licença da ferramenta
e o aceite de uso e então chama estas rotas com o token de serviço. O tenant é o UUID da empresa
no portal.
"""
import hmac
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from .api import cache_qr, instance_public
from .callbacks import bot_destination
from .core import fail, new_id, now
from .providers import ProviderError

router = APIRouter(prefix='/service/v1/tenants/{tenant_id}/whatsapp', include_in_schema=False)
INSTANCE_NAME = 'WhatsApp principal'
KEY_SCOPES = ['instances:read', 'messages:read', 'messages:send']
MAX_ACTIVE_KEYS = 10


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class InstanceRequest(StrictModel):
    company_name: str = Field(min_length=1, max_length=200)


class WebhookRequest(StrictModel):
    url: str = Field(min_length=12, max_length=2048)
    include_message: bool = True
    rotate_secret: bool = False


class KeyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    expires_in_days: int = Field(default=365, ge=1, le=365)


async def portal_tenant(tenant_id: UUID, request: Request) -> str:
    rt = request.app.state.rt
    expected = rt.config.portal_service_token.get_secret_value()
    supplied = request.headers.get('authorization', '')
    if len(expected) < 32 or not hmac.compare_digest(supplied.encode(), f'Bearer {expected}'.encode()):
        fail(401, 'UNAUTHORIZED', 'Serviço não autorizado.')
    return str(tenant_id)


Tenant = Annotated[str, Depends(portal_tenant)]


def actor(request: Request) -> str:
    value = request.headers.get('x-actor-id', '')
    try:
        return f'portal:{UUID(value)}'
    except ValueError:
        return 'portal'


async def current_instance(rt, tenant: str):
    return await rt.db.instances.find_one({'tenant_id': tenant, 'connection_state': {'$ne': 'deleting'}},
                                          sort=[('created_at', 1)])


async def required_instance(rt, tenant: str):
    doc = await current_instance(rt, tenant)
    if not doc:
        fail(409, 'INSTANCE_REQUIRED', 'Crie a conexão de WhatsApp primeiro.')
    return doc


async def refresh_connection(rt, doc):
    if doc['connection_state'] in ('error', 'creating', 'deleting'):
        return doc
    try:
        state = await rt.provider.connection(doc['provider_ref'])
    except ProviderError:
        return doc
    update = {'connection_state': state, 'observed_at': now(), 'last_error': None}
    if state != doc['connection_state'] and state in ('connected', 'disconnected'):
        update['last_connected_at' if state == 'connected' else 'last_disconnected_at'] = now()
    await rt.db.instances.update_one({'_id': doc['_id'], 'tenant_id': doc['tenant_id']}, {'$set': update})
    if state == 'connected':
        await rt.redis.delete(f'qr:{doc["_id"]}')
    return {**doc, **update}


def webhook_public(doc):
    if not doc:
        return None
    return {'url': doc['url'], 'include_message': doc.get('include_message', False),
            'updated_at': doc.get('updated_at')}


def key_public(doc):
    return {'id': doc['_id'], 'name': doc['name'], 'prefix': doc['prefix'], 'created_at': doc['created_at'],
            'expires_at': doc['expires_at'], 'revoked_at': doc.get('revoked_at')}


async def overview(rt, tenant: str, instance):
    webhook = keys = None
    if instance:
        webhook = await rt.db.callback_destinations.find_one(
            {'tenant_id': tenant, 'instance_id': instance['_id'], 'enabled': True})
        keys = await rt.db.api_keys.find({'tenant_id': tenant, 'instance_ids': instance['_id'],
            'revoked_at': None, 'expires_at': {'$gt': now()}}).sort('created_at', -1).to_list(MAX_ACTIVE_KEYS)
    return {'instance': instance_public(instance) if instance else None,
            'webhook': webhook_public(webhook), 'keys': [key_public(doc) for doc in keys or []]}


@router.get('')
async def status(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    instance = await current_instance(rt, tenant)
    if instance:
        instance = await refresh_connection(rt, instance)
    return await overview(rt, tenant, instance)


@router.post('/instance')
async def ensure_instance(body: InstanceRequest, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    await rt.db.tenants.update_one({'_id': tenant}, {'$set': {'name': body.company_name, 'source': 'futuranext',
        'updated_at': now()}, '$setOnInsert': {'created_at': now()}}, upsert=True)
    doc = await current_instance(rt, tenant)
    if doc and doc['connection_state'] != 'error':
        return await overview(rt, tenant, doc)
    await rt.limit(f'service-create:{tenant}', 5)
    retrying = doc is not None
    if not doc:
        instance_id = new_id()
        doc = {'_id': instance_id, 'tenant_id': tenant, 'name': INSTANCE_NAME, 'client_label': body.company_name[:100],
            'provider': 'evolution', 'provider_ref': f'wp_{instance_id}', 'connection_state': 'creating',
            'created_at': now(), 'schema_version': 1}
        try:
            await rt.db.instances.insert_one(doc)
        except DuplicateKeyError:
            return await overview(rt, tenant, await current_instance(rt, tenant))
        await rt.audit(tenant, actor(request), 'instance.create', instance_id)
    # Um segredo novo por tentativa: a Evolution recebe o mesmo valor gravado como hash.
    secret = secrets.token_urlsafe(32)
    await rt.db.instances.update_one({'_id': doc['_id'], 'tenant_id': tenant},
        {'$set': {'webhook_hash': rt.digest(secret), 'webhook_secret': rt.seal(secret)}})
    try:
        if retrying:
            # A tentativa anterior pode ter criado a instância no gateway sem confirmação.
            await rt.provider.delete(doc['provider_ref'])
        await rt.provider.create(doc['provider_ref'],
            f'{rt.config.webhook_base_url}/internal/webhooks/evolution/{doc["_id"]}', secret)
        changes = {'connection_state': 'disconnected', 'last_error': None}
    except ProviderError as exc:
        changes = {'connection_state': 'error', 'last_error': exc.code}
    await rt.db.instances.update_one({'_id': doc['_id'], 'tenant_id': tenant}, {'$set': changes})
    if changes['connection_state'] == 'error':
        fail(503, changes['last_error'], 'O gateway não confirmou a criação. Tente novamente em instantes.')
    return await overview(rt, tenant, {**doc, **changes})


@router.post('/connect', status_code=202)
async def connect(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await refresh_connection(rt, await required_instance(rt, tenant))
    if doc['connection_state'] == 'connected':
        return {'status': 'connected'}
    if doc['connection_state'] in ('creating', 'error'):
        fail(409, 'INSTANCE_NOT_READY', 'A conexão ainda não está pronta para o pareamento.')
    await rt.limit(f'connect:{doc["_id"]}', 3)
    await rt.audit(tenant, actor(request), 'instance.connect', doc['_id'])
    await cache_qr(rt, doc['_id'], await rt.provider.connect(doc['provider_ref']))
    return {'status': 'pairing_requested'}


@router.get('/qr')
async def qr(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    value = await rt.redis.get(f'qr:{doc["_id"]}')
    if not value or doc['connection_state'] == 'connected':
        fail(409, 'QR_UNAVAILABLE', 'O QR Code ainda não está disponível ou expirou.')
    return rt.unseal(value)


@router.post('/disconnect')
async def disconnect(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    await rt.provider.disconnect(doc['provider_ref'])
    await rt.redis.delete(f'qr:{doc["_id"]}')
    changes = {'connection_state': 'disconnected', 'last_disconnected_at': now()}
    await rt.db.instances.update_one({'_id': doc['_id'], 'tenant_id': tenant}, {'$set': changes})
    await rt.audit(tenant, actor(request), 'instance.disconnect', doc['_id'])
    return await overview(rt, tenant, {**doc, **changes})


@router.put('/webhook')
async def set_webhook(body: WebhookRequest, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    query = {'_id': f'{tenant}:{doc["_id"]}:bot'}
    existing = await rt.db.callback_destinations.find_one(query)
    issued = None
    if existing and existing.get('enabled') and not body.rotate_secret:
        secret = rt.unseal(existing['secret'])
    else:
        secret = issued = 'whsec_' + secrets.token_urlsafe(32)
    try:
        bot_destination({'tenant_id': tenant, 'instance_id': doc['_id'], 'url': body.url,
                         'include_message': body.include_message}, secret)
    except ValueError:
        fail(422, 'WEBHOOK_URL_INVALID',
             'Use uma URL HTTPS pública, com domínio, porta padrão e sem parâmetros (?) ou fragmento (#).')
    stored = {'tenant_id': tenant, 'instance_id': doc['_id'], 'kind': 'bot', 'url': body.url,
              'include_message': body.include_message, 'secret': rt.seal(secret), 'enabled': True,
              'updated_at': now()}
    await rt.db.callback_destinations.update_one(query, {'$set': stored}, upsert=True)
    await rt.audit(tenant, actor(request), 'webhook.rotate' if issued and existing else 'webhook.save', doc['_id'])
    return {**await overview(rt, tenant, doc), 'webhook_secret': issued}


@router.delete('/webhook')
async def delete_webhook(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    await rt.db.callback_destinations.delete_one({'_id': f'{tenant}:{doc["_id"]}:bot'})
    await rt.audit(tenant, actor(request), 'webhook.delete', doc['_id'])
    return await overview(rt, tenant, doc)


@router.post('/keys', status_code=201)
async def create_key(body: KeyRequest, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    active = {'tenant_id': tenant, 'revoked_at': None, 'expires_at': {'$gt': now()}}
    if await rt.db.api_keys.count_documents(active) >= MAX_ACTIVE_KEYS:
        fail(409, 'KEY_LIMIT', f'Revogue uma chave antes de criar outra. Limite de {MAX_ACTIVE_KEYS} chaves ativas.')
    secret = 'wp_' + secrets.token_urlsafe(32)
    key = {'_id': new_id(), 'tenant_id': tenant, 'name': body.name, 'prefix': secret[:10],
        'secret_hash': rt.digest(secret), 'scopes': KEY_SCOPES, 'instance_ids': [doc['_id']],
        'created_at': now(), 'expires_at': now() + timedelta(days=body.expires_in_days), 'revoked_at': None}
    await rt.db.api_keys.insert_one(key)
    await rt.audit(tenant, actor(request), 'key.create', key['_id'])
    return {**await overview(rt, tenant, doc), 'api_key': secret}


@router.delete('/keys/{key_id}')
async def revoke_key(key_id: str, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    result = await rt.db.api_keys.update_one({'_id': key_id, 'tenant_id': tenant, 'revoked_at': None},
                                            {'$set': {'revoked_at': now()}})
    if not result.matched_count:
        fail(404, 'NOT_FOUND', 'Chave não encontrada.')
    await rt.audit(tenant, actor(request), 'key.revoke', key_id)
    return await overview(rt, tenant, await current_instance(rt, tenant))
