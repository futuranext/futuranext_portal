"""API interna usada somente pela API do portal FuturaNext.

A rota não é publicada pelo proxy. O portal autentica o usuário, confere a licença da ferramenta
e o aceite de uso e então chama estas rotas com o token de serviço. O tenant é o UUID da empresa
no portal (futuranext.organizations). Como defesa adicional, o banco confirma a licença vigente
antes de criar ou conectar e o aceite do usuário (X-Actor-Id) antes de exibir o QR Code.
"""
import hmac
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field

from .api import cache_qr, connection_changes, instance_public, update_instance
from .callbacks import bot_destination
from .core import as_uuid, fail, fetch_all, fetch_one, new_id, now
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


def actor_id(request: Request) -> UUID | None:
    return as_uuid(request.headers.get('x-actor-id', ''))


def actor(request: Request) -> str:
    value = actor_id(request)
    return f'portal:{value}' if value else 'portal'


async def require_license(rt, tenant: str):
    async with rt.db.tx(tenant) as conn:
        row = await fetch_one(conn, 'SELECT whatsapp.tenant_licensed(%s) AS ok', (tenant,))
    if not row['ok']:
        fail(409, 'LICENSE_REQUIRED', 'A ferramenta WhatsApp não está liberada para esta empresa.')


async def require_terms(rt, tenant: str, request: Request):
    user = actor_id(request)
    accepted = False
    if user:
        async with rt.db.tx(tenant) as conn:
            row = await fetch_one(conn, 'SELECT whatsapp.terms_accepted(%s, %s) AS ok', (tenant, user))
        accepted = row['ok']
    if not accepted:
        fail(409, 'TERMS_REQUIRED', 'Aceite os termos de uso pelo link enviado por e-mail antes de parear.')


async def current_instance(rt, tenant: str):
    async with rt.db.tx(tenant) as conn:
        return await fetch_one(conn, '''
            SELECT * FROM whatsapp.instances WHERE tenant_id = %s AND connection_state <> 'deleting'
            ORDER BY created_at, id LIMIT 1''', (tenant,))


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
    changes = connection_changes(doc, state)
    await update_instance(rt, doc['tenant_id'], doc['id'], changes)
    if state == 'connected':
        await rt.redis.delete(f'qr:{doc["id"]}')
    return {**doc, **changes}


def webhook_public(doc):
    if not doc:
        return None
    return {'url': doc['url'], 'include_message': doc['include_message'], 'updated_at': doc['updated_at']}


def key_public(doc):
    return {'id': str(doc['id']), 'name': doc['name'], 'prefix': doc['prefix'], 'created_at': doc['created_at'],
            'expires_at': doc['expires_at'], 'revoked_at': doc['revoked_at']}


async def overview(rt, tenant: str, instance):
    webhook, keys = None, []
    if instance:
        async with rt.db.tx(tenant) as conn:
            webhook = await fetch_one(conn, '''
                SELECT url, include_message, updated_at FROM whatsapp.callback_destinations
                WHERE tenant_id = %s AND instance_id = %s AND kind = 'bot' AND enabled''', (tenant, instance['id']))
            keys = await fetch_all(conn, '''
                SELECT key.* FROM whatsapp.api_keys key
                JOIN whatsapp.api_key_instances link ON link.key_id = key.id AND link.tenant_id = key.tenant_id
                WHERE key.tenant_id = %s AND link.instance_id = %s AND key.revoked_at IS NULL AND key.expires_at > now()
                ORDER BY key.created_at DESC LIMIT %s''', (tenant, instance['id'], MAX_ACTIVE_KEYS))
    return {'instance': instance_public(instance) if instance else None,
            'webhook': webhook_public(webhook), 'keys': [key_public(doc) for doc in keys]}


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
    doc = await current_instance(rt, tenant)
    if doc and doc['connection_state'] != 'error':
        return await overview(rt, tenant, doc)
    await require_license(rt, tenant)
    await rt.limit(f'service-create:{tenant}', 5)
    retrying = doc is not None
    # Um segredo novo por tentativa: a Evolution recebe o mesmo valor gravado como hash.
    secret = secrets.token_urlsafe(32)
    try:
        async with rt.db.tx(tenant) as conn:
            if doc:
                doc = await fetch_one(conn, '''
                    UPDATE whatsapp.instances SET webhook_hash = %s, webhook_secret = %s
                    WHERE id = %s AND tenant_id = %s RETURNING *''',
                    (rt.digest(secret), rt.seal(secret), doc['id'], tenant))
            else:
                instance_id = new_id()
                doc = await fetch_one(conn, '''
                    INSERT INTO whatsapp.instances (id, tenant_id, name, client_label, provider_ref, connection_state,
                        webhook_hash, webhook_secret)
                    VALUES (%s, %s, %s, %s, %s, 'creating', %s, %s) RETURNING *''',
                    (instance_id, tenant, INSTANCE_NAME, body.company_name[:100], f'wp_{instance_id}',
                     rt.digest(secret), rt.seal(secret)))
                await rt.audit(conn, tenant, actor(request), 'instance.create', instance_id)
    except errors.UniqueViolation:
        return await overview(rt, tenant, await current_instance(rt, tenant))
    except errors.ForeignKeyViolation:
        fail(404, 'TENANT_NOT_FOUND', 'Empresa não encontrada no portal.')
    try:
        if retrying:
            # A tentativa anterior pode ter criado a instância no gateway sem confirmação.
            await rt.provider.delete(doc['provider_ref'])
        await rt.provider.create(doc['provider_ref'],
            f'{rt.config.webhook_base_url}/internal/webhooks/evolution/{doc["id"]}', secret)
        changes = {'connection_state': 'disconnected', 'last_error': None}
    except ProviderError as exc:
        changes = {'connection_state': 'error', 'last_error': exc.code}
    await update_instance(rt, tenant, doc['id'], changes)
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
    await require_license(rt, tenant)
    await require_terms(rt, tenant, request)
    await rt.limit(f'connect:{doc["id"]}', 3)
    async with rt.db.tx(tenant) as conn:
        await rt.audit(conn, tenant, actor(request), 'instance.connect', doc['id'])
    await cache_qr(rt, doc['id'], await rt.provider.connect(doc['provider_ref']))
    return {'status': 'pairing_requested'}


@router.get('/qr')
async def qr(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    await require_terms(rt, tenant, request)
    value = await rt.redis.get(f'qr:{doc["id"]}')
    if not value or doc['connection_state'] == 'connected':
        fail(409, 'QR_UNAVAILABLE', 'O QR Code ainda não está disponível ou expirou.')
    return rt.unseal(value)


@router.post('/disconnect')
async def disconnect(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    await rt.provider.disconnect(doc['provider_ref'])
    await rt.redis.delete(f'qr:{doc["id"]}')
    changes = {'connection_state': 'disconnected', 'last_disconnected_at': now()}
    async with rt.db.tx(tenant) as conn:
        await conn.execute('UPDATE whatsapp.instances SET connection_state = %s, last_disconnected_at = %s '
                           'WHERE id = %s AND tenant_id = %s',
                           (changes['connection_state'], changes['last_disconnected_at'], doc['id'], tenant))
        await rt.audit(conn, tenant, actor(request), 'instance.disconnect', doc['id'])
    return await overview(rt, tenant, {**doc, **changes})


@router.put('/webhook')
async def set_webhook(body: WebhookRequest, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    await require_license(rt, tenant)
    async with rt.db.tx(tenant) as conn:
        existing = await fetch_one(conn, '''
            SELECT secret, enabled FROM whatsapp.callback_destinations
            WHERE tenant_id = %s AND instance_id = %s AND kind = 'bot' FOR UPDATE''', (tenant, doc['id']))
        issued = None
        if existing and existing['enabled'] and not body.rotate_secret:
            secret = rt.unseal(existing['secret'])
        else:
            secret = issued = 'whsec_' + secrets.token_urlsafe(32)
        try:
            bot_destination({'tenant_id': tenant, 'instance_id': doc['id'], 'url': body.url,
                             'include_message': body.include_message}, secret)
        except ValueError:
            fail(422, 'WEBHOOK_URL_INVALID',
                 'Use uma URL HTTPS pública, com domínio, porta padrão e sem parâmetros (?) ou fragmento (#).')
        await conn.execute('''
            INSERT INTO whatsapp.callback_destinations (tenant_id, instance_id, kind, url, include_message, secret,
                enabled, updated_at)
            VALUES (%s, %s, 'bot', %s, %s, %s, true, now())
            ON CONFLICT (tenant_id, instance_id, kind) DO UPDATE SET url = EXCLUDED.url,
                include_message = EXCLUDED.include_message, secret = EXCLUDED.secret, enabled = true,
                updated_at = EXCLUDED.updated_at''',
            (tenant, doc['id'], body.url, body.include_message, rt.seal(secret)))
        await rt.audit(conn, tenant, actor(request), 'webhook.rotate' if issued and existing else 'webhook.save', doc['id'])
    return {**await overview(rt, tenant, doc), 'webhook_secret': issued}


@router.delete('/webhook')
async def delete_webhook(tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    async with rt.db.tx(tenant) as conn:
        await conn.execute("DELETE FROM whatsapp.callback_destinations WHERE tenant_id = %s AND instance_id = %s "
                           "AND kind = 'bot'", (tenant, doc['id']))
        await rt.audit(conn, tenant, actor(request), 'webhook.delete', doc['id'])
    return await overview(rt, tenant, doc)


@router.post('/keys', status_code=201)
async def create_key(body: KeyRequest, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    doc = await required_instance(rt, tenant)
    await require_license(rt, tenant)
    secret = 'wp_' + secrets.token_urlsafe(32)
    key_id = new_id()
    async with rt.db.tx(tenant) as conn:
        # Serializa a contagem por empresa para o limite valer também com pedidos simultâneos.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('whatsapp.keys:' || %s))", (tenant,))
        active = await fetch_one(conn, 'SELECT count(*) AS total FROM whatsapp.api_keys WHERE tenant_id = %s '
                                 'AND revoked_at IS NULL AND expires_at > now()', (tenant,))
        if active['total'] >= MAX_ACTIVE_KEYS:
            fail(409, 'KEY_LIMIT', f'Revogue uma chave antes de criar outra. Limite de {MAX_ACTIVE_KEYS} chaves ativas.')
        await conn.execute('''
            INSERT INTO whatsapp.api_keys (id, tenant_id, name, prefix, secret_hash, scopes, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(days => %s))''',
            (key_id, tenant, body.name, secret[:10], rt.digest(secret), KEY_SCOPES, body.expires_in_days))
        await conn.execute('INSERT INTO whatsapp.api_key_instances (tenant_id, key_id, instance_id) VALUES (%s, %s, %s)',
                           (tenant, key_id, doc['id']))
        await rt.audit(conn, tenant, actor(request), 'key.create', key_id)
    return {**await overview(rt, tenant, doc), 'api_key': secret}


@router.delete('/keys/{key_id}')
async def revoke_key(key_id: str, tenant: Tenant, request: Request):
    rt = request.app.state.rt
    async with rt.db.tx(tenant) as conn:
        result = await conn.execute('UPDATE whatsapp.api_keys SET revoked_at = now() WHERE id = %s AND tenant_id = %s '
                                    'AND revoked_at IS NULL', (as_uuid(key_id), tenant)) if as_uuid(key_id) else None
        if not result or not result.rowcount:
            fail(404, 'NOT_FOUND', 'Chave não encontrada.')
        await rt.audit(conn, tenant, actor(request), 'key.revoke', key_id)
    return await overview(rt, tenant, await current_instance(rt, tenant))
