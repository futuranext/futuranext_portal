"""API de negócio: acesso sempre limitado ao tenant e à instância autorizada."""
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field

from .auth import check_instance, identity
from .core import as_uuid, fail, fetch_all, fetch_one, new_id, now
from .providers import ProviderError

router = APIRouter(prefix='/api/v1')
internal = APIRouter(prefix='/internal/webhooks')
PHONE = r'^[1-9][0-9]{7,14}$'
QUEUED = ('queued', 'retry_wait')
INSTANCE_FIELDS = ('id', 'name', 'client_label', 'connection_state', 'phone_masked', 'last_connected_at',
                   'last_disconnected_at', 'created_at', 'last_error', 'observed_at')


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
    return {key: doc.get(key) for key in INSTANCE_FIELDS} | {'id': str(doc['id'])}


async def instance_row(conn, tenant, instance_id):
    return await fetch_one(conn, 'SELECT * FROM whatsapp.instances WHERE id = %s AND tenant_id = %s',
                           (instance_id, tenant))


async def update_instance(rt, tenant, instance_id, changes: dict, *, guard_deleting=False):
    assignments = ', '.join(f'{column} = %s' for column in changes)
    query = f'UPDATE whatsapp.instances SET {assignments} WHERE id = %s AND tenant_id = %s'
    if guard_deleting:
        query += " AND connection_state <> 'deleting'"
    async with rt.db.tx(tenant) as conn:
        await conn.execute(query, (*changes.values(), instance_id, tenant))


async def owned(request, scope, instance_id):
    user = await identity(request, scope)
    check_instance(user, str(instance_id))
    rt = request.app.state.rt
    key = as_uuid(instance_id)
    doc = None
    if key:
        async with rt.db.tx(user.tenant_id) as conn:
            doc = await instance_row(conn, user.tenant_id, key)
    if not doc:
        fail(404, 'NOT_FOUND', 'Instância não encontrada.')
    return rt, user, doc


async def cache_qr(rt, instance_id, data):
    qr = data.get('qrcode', data) if isinstance(data, dict) else {}
    image = qr.get('base64', '') if isinstance(qr, dict) else ''
    if isinstance(image, str) and image.startswith('data:image/png;base64,') and len(image) < 400_000:
        await rt.redis.set(f'qr:{instance_id}', rt.seal({'base64': image,
            'expires_at': (now() + timedelta(seconds=60)).isoformat()}), ex=60)


def connection_changes(doc, state):
    changes = {'connection_state': state, 'observed_at': now(), 'last_error': None}
    if state != doc['connection_state'] and state in ('connected', 'disconnected'):
        changes['last_connected_at' if state == 'connected' else 'last_disconnected_at'] = now()
    return changes


async def cancel_queued(conn, tenant, code, *, instance_id=None, phone_hash=None):
    query = ('UPDATE whatsapp.messages SET status = %s, error_code = %s, updated_at = now() '
             'WHERE tenant_id = %s AND status = ANY(%s)')
    params = ['cancelled', code, tenant, list(QUEUED)]
    if instance_id:
        query += ' AND instance_id = %s'
        params.append(instance_id)
    if phone_hash:
        query += ' AND phone_hash = %s'
        params.append(phone_hash)
    await conn.execute(query, params)


@router.get('/instances', tags=['Instâncias'])
async def instances(request: Request):
    user = await identity(request, 'instances:read')
    rt = request.app.state.rt
    async with rt.db.tx(user.tenant_id) as conn:
        docs = await fetch_all(conn, 'SELECT * FROM whatsapp.instances WHERE tenant_id = %s '
                               'AND (%s::uuid[] IS NULL OR id = ANY(%s::uuid[])) '
                               'ORDER BY created_at DESC, id DESC LIMIT 100',
                               (user.tenant_id, user.instance_ids, user.instance_ids))
    return {'items': [instance_public(doc) for doc in docs]}


@router.post('/instances', status_code=201, tags=['Instâncias'])
async def create_instance(body: InstanceCreate, request: Request):
    user = await identity(request, 'instances:write')
    rt = request.app.state.rt
    instance_id, secret = new_id(), secrets.token_urlsafe(32)
    try:
        async with rt.db.tx(user.tenant_id) as conn:
            doc = await fetch_one(conn, '''
                INSERT INTO whatsapp.instances (id, tenant_id, name, client_label, provider_ref,
                    connection_state, webhook_hash, webhook_secret)
                VALUES (%s, %s, %s, %s, %s, 'creating', %s, %s) RETURNING *''',
                (instance_id, user.tenant_id, body.name, body.client_label, f'wp_{instance_id}',
                 rt.digest(secret), rt.seal(secret)))
            await rt.audit(conn, user.tenant_id, user.actor_id, 'instance.create', instance_id)
    except errors.UniqueViolation:
        fail(409, 'INSTANCE_EXISTS', 'Já existe uma instância com esse nome.')
    try:
        await rt.provider.create(doc['provider_ref'],
            f'{rt.config.webhook_base_url}/internal/webhooks/evolution/{instance_id}', secret)
        await update_instance(rt, user.tenant_id, instance_id, {'connection_state': 'disconnected'})
    except ProviderError as exc:
        await update_instance(rt, user.tenant_id, instance_id, {'connection_state': 'error', 'last_error': exc.code})
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
    async with rt.db.tx(user.tenant_id) as conn:
        await rt.audit(conn, user.tenant_id, user.actor_id, 'instance.connect', instance_id)
    data = await rt.provider.connect(doc['provider_ref'])
    await cache_qr(rt, instance_id, data)
    return {'status': 'pairing_requested'}


@router.get('/instances/{instance_id}/qr', tags=['Instâncias'])
async def qr(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:connect', instance_id)
    if not user.session:
        fail(403, 'SESSION_REQUIRED', 'Pareamento exige login no painel.')
    value = await rt.redis.get(f'qr:{doc["id"]}')
    if not value or doc['connection_state'] == 'connected':
        fail(409, 'QR_UNAVAILABLE', 'QR Code ainda não disponível ou já expirou.')
    return rt.unseal(value)


@router.get('/instances/{instance_id}/connection', tags=['Instâncias'])
async def connection(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:read', instance_id)
    if doc['connection_state'] not in ('error', 'creating', 'deleting'):
        state = await rt.provider.connection(doc['provider_ref'])
        changes = connection_changes(doc, state)
        await update_instance(rt, user.tenant_id, doc['id'], changes)
        doc.update(changes)
        if state == 'connected':
            await rt.redis.delete(f'qr:{doc["id"]}')
    return instance_public(doc)


@router.post('/instances/{instance_id}/disconnect', tags=['Instâncias'])
async def disconnect(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:write', instance_id)
    await rt.provider.disconnect(doc['provider_ref'])
    await rt.redis.delete(f'qr:{doc["id"]}')
    async with rt.db.tx(user.tenant_id) as conn:
        await conn.execute("UPDATE whatsapp.instances SET connection_state = 'disconnected', "
                           'last_disconnected_at = now() WHERE id = %s AND tenant_id = %s', (doc['id'], user.tenant_id))
        await rt.audit(conn, user.tenant_id, user.actor_id, 'instance.disconnect', doc['id'])
    return {'status': 'disconnected'}


@router.delete('/instances/{instance_id}', status_code=204, tags=['Instâncias'])
async def delete_instance(instance_id: str, request: Request):
    rt, user, doc = await owned(request, 'instances:delete', instance_id)
    await update_instance(rt, user.tenant_id, doc['id'], {'connection_state': 'deleting'})
    await rt.provider.delete(doc['provider_ref'])
    async with rt.db.tx(user.tenant_id) as conn:
        await cancel_queued(conn, user.tenant_id, 'INSTANCE_DELETED', instance_id=doc['id'])
        # Chaves, eventos, entregas e webhook da instância saem por FK em cascata.
        await conn.execute('DELETE FROM whatsapp.instances WHERE id = %s AND tenant_id = %s', (doc['id'], user.tenant_id))
        await rt.audit(conn, user.tenant_id, user.actor_id, 'instance.delete', doc['id'])
    await rt.redis.delete(f'qr:{doc["id"]}')


async def recipient_allowed(conn, tenant_id, phone_hash):
    """O aceite é do titular que pareou o número (portal FuturaNext); opt-out sempre bloqueia."""
    row = await fetch_one(conn, 'SELECT 1 FROM whatsapp.suppressions WHERE tenant_id = %s AND phone_hash = %s',
                          (tenant_id, phone_hash))
    return row is None


async def suppress(rt, conn, tenant, phone_hash, reason):
    await conn.execute('''
        INSERT INTO whatsapp.suppressions (tenant_id, phone_hash, opted_out_at, reason) VALUES (%s, %s, now(), %s)
        ON CONFLICT (tenant_id, phone_hash) DO UPDATE SET opted_out_at = EXCLUDED.opted_out_at, reason = EXCLUDED.reason''',
        (tenant, phone_hash, rt.seal(reason)))
    await cancel_queued(conn, tenant, 'OPTED_OUT', phone_hash=phone_hash)


@router.post('/consents', status_code=201, tags=['Consentimento'])
async def consent(body: Consent, request: Request):
    await identity(request, 'consents:write')
    fail(410, 'EMAIL_CONSENT_ONLY', 'A autorização deve ser confirmada pelo destinatário via e-mail.')


@router.post('/opt-outs', status_code=201, tags=['Consentimento'])
async def opt_out(body: OptOut, request: Request):
    user = await identity(request, 'consents:write')
    rt = request.app.state.rt
    phone_hash = rt.digest(f'{user.tenant_id}:{body.phone}')
    async with rt.db.tx(user.tenant_id) as conn:
        await suppress(rt, conn, user.tenant_id, phone_hash, body.reason)
        await rt.audit(conn, user.tenant_id, user.actor_id, 'consent.opt_out')
    return {'status': 'blocked'}


def message_public(rt, doc):
    content = (rt.unseal(doc['content']) if doc.get('content') and doc['content_expires_at'] > now()
               else {'phone': '', 'message': '[Conteúdo expirado]'})
    return {'id': str(doc['id']), 'instance_id': str(doc['instance_id']), 'direction': doc['direction'],
            'status': doc['status'], 'created_at': doc['created_at'], 'error_code': doc.get('error_code'),
            'provider_message_id': doc.get('provider_message_id'), **content}


@router.post('/messages/send', status_code=202, tags=['Mensagens'])
async def send_text(body: SendText, request: Request, response: Response):
    rt, user, instance = await owned(request, 'messages:send', body.instance_id)
    tenant, instance_id = user.tenant_id, instance['id']
    payload_hash = rt.digest(json.dumps(body.model_dump(exclude={'idempotency_key'}), sort_keys=True, ensure_ascii=False))
    lookup = ('SELECT id, status, created_at, payload_hash FROM whatsapp.messages WHERE tenant_id = %s '
              "AND instance_id = %s AND idempotency_key = %s AND direction = 'outbound'")
    key = (tenant, instance_id, body.idempotency_key)

    def replay(existing):
        if existing['payload_hash'] != payload_hash:
            fail(409, 'IDEMPOTENCY_CONFLICT', 'Esta chave já foi usada com outro conteúdo.')
        response.status_code = 200
        return {'id': str(existing['id']), 'status': existing['status'], 'created_at': existing['created_at']}

    phone_hash = rt.digest(f'{tenant}:{body.phone}')
    async with rt.db.tx(tenant) as conn:
        existing = await fetch_one(conn, lookup, key)
        allowed = existing is None and await recipient_allowed(conn, tenant, phone_hash)
    if existing:
        return replay(existing)
    if not allowed:
        fail(403, 'OPTED_OUT', 'Este destinatário pediu para não receber mensagens.')
    if instance['connection_state'] != 'connected':
        fail(409, 'NOT_CONNECTED', 'A instância precisa estar conectada para enviar.')
    await rt.limit(f'enqueue:{tenant}', 30)
    message_id, created = new_id(), now()
    async with rt.db.tx(tenant) as conn:
        inserted = await fetch_one(conn, '''
            INSERT INTO whatsapp.messages (id, tenant_id, instance_id, direction, status, idempotency_key,
                payload_hash, phone_hash, content, created_at, due_at, attempts, content_expires_at, expires_at)
            VALUES (%s, %s, %s, 'outbound', 'queued', %s, %s, %s, %s, %s, %s, 0, %s, %s)
            ON CONFLICT (tenant_id, instance_id, idempotency_key) WHERE direction = 'outbound' DO NOTHING
            RETURNING id''',
            (message_id, tenant, instance_id, body.idempotency_key, payload_hash, phone_hash,
             rt.seal({'phone': body.phone, 'message': body.message}), created, created,
             created + timedelta(days=30), created + timedelta(days=90)))
        if inserted:
            await rt.audit(conn, tenant, user.actor_id, 'message.queued', message_id)
        else:
            existing = await fetch_one(conn, lookup, key)
    if not inserted:
        return replay(existing)
    try:
        await rt.redis.xadd('dispatch', {'id': message_id}, maxlen=10000)
    except Exception:
        pass  # A linha durável será encontrada pelo dispatcher; Redis só sinaliza.
    response.headers['Location'] = f'/api/v1/messages/{message_id}'
    return {'id': message_id, 'status': 'queued', 'created_at': created}


def messages_scope(user):
    return ('tenant_id = %s AND expires_at > now() AND (%s::uuid[] IS NULL OR instance_id = ANY(%s::uuid[]))',
            [user.tenant_id, user.instance_ids, user.instance_ids])


@router.get('/messages', tags=['Mensagens'])
async def messages(request: Request, instance_id: str | None = None, phone: str | None = Query(None, pattern=PHONE),
                   status: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                   cursor: str | None = None, limit: int = Query(25, ge=1, le=100)):
    user = await identity(request, 'messages:read')
    rt = request.app.state.rt
    where, params = messages_scope(user)
    conditions = [where]
    if instance_id:
        check_instance(user, instance_id)
        if not as_uuid(instance_id):
            return {'items': [], 'next_cursor': None}
        conditions.append('instance_id = %s')
        params.append(as_uuid(instance_id))
    if phone:
        conditions.append('phone_hash = %s')
        params.append(rt.digest(f'{user.tenant_id}:{phone}'))
    if status:
        conditions.append('status = %s')
        params.append(status)
    if date_from:
        conditions.append('created_at >= %s')
        params.append(date_from)
    if date_to:
        conditions.append('created_at <= %s')
        params.append(date_to)
    async with rt.db.tx(user.tenant_id) as conn:
        if cursor:
            scope, scope_params = messages_scope(user)
            previous = await fetch_one(conn, f'SELECT id, created_at FROM whatsapp.messages WHERE {scope} AND id = %s',
                                       (*scope_params, as_uuid(cursor))) if as_uuid(cursor) else None
            if not previous:
                fail(422, 'CURSOR_INVALID', 'Cursor inválido.')
            conditions.append('(created_at, id) < (%s, %s)')
            params.extend([previous['created_at'], previous['id']])
        docs = await fetch_all(conn, f'SELECT * FROM whatsapp.messages WHERE {" AND ".join(conditions)} '
                               'ORDER BY created_at DESC, id DESC LIMIT %s', (*params, limit + 1))
    return {'items': [message_public(rt, d) for d in docs[:limit]],
            'next_cursor': str(docs[limit - 1]['id']) if len(docs) > limit else None}


@router.get('/messages/{message_id}', tags=['Mensagens'])
async def message_status(message_id: str, request: Request):
    user = await identity(request, 'messages:read')
    rt = request.app.state.rt
    doc = None
    if as_uuid(message_id):
        where, params = messages_scope(user)
        async with rt.db.tx(user.tenant_id) as conn:
            doc = await fetch_one(conn, f'SELECT * FROM whatsapp.messages WHERE {where} AND id = %s',
                                  (*params, as_uuid(message_id)))
    if not doc:
        fail(404, 'NOT_FOUND', 'Mensagem não encontrada.')
    return message_public(rt, doc)


@router.post('/api-keys', status_code=201, tags=['API Keys'])
async def create_key(body: KeyCreate, request: Request):
    user = await identity(request, 'keys:write')
    rt = request.app.state.rt
    ids = list({as_uuid(item) for item in body.instance_ids})
    secret = 'wp_' + secrets.token_urlsafe(32)
    key_id, expires = new_id(), now() + timedelta(days=body.expires_in_days)
    async with rt.db.tx(user.tenant_id) as conn:
        found = await fetch_one(conn, 'SELECT count(*) AS total FROM whatsapp.instances WHERE tenant_id = %s AND id = ANY(%s)',
                                (user.tenant_id, [item for item in ids if item]))
        if None in ids or found['total'] != len(ids):
            fail(404, 'NOT_FOUND', 'Uma das instâncias não foi encontrada.')
        await conn.execute('INSERT INTO whatsapp.api_keys (id, tenant_id, name, prefix, secret_hash, scopes, expires_at) '
                           'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                           (key_id, user.tenant_id, body.name, secret[:10], rt.digest(secret), list(body.scopes), expires))
        for item in ids:
            await conn.execute('INSERT INTO whatsapp.api_key_instances (tenant_id, key_id, instance_id) VALUES (%s, %s, %s)',
                               (user.tenant_id, key_id, item))
        await rt.audit(conn, user.tenant_id, user.actor_id, 'key.create', key_id)
    return {'id': key_id, 'secret': secret, 'expires_at': expires}


@router.get('/api-keys', tags=['API Keys'])
async def keys(request: Request):
    user = await identity(request, 'keys:read')
    rt = request.app.state.rt
    async with rt.db.tx(user.tenant_id) as conn:
        docs = await fetch_all(conn, '''
            SELECT key.id, key.name, key.prefix, key.scopes, key.created_at, key.expires_at, key.revoked_at,
                   coalesce(array_agg(link.instance_id::text ORDER BY link.instance_id)
                            FILTER (WHERE link.instance_id IS NOT NULL), '{}') AS instance_ids
            FROM whatsapp.api_keys key
            LEFT JOIN whatsapp.api_key_instances link ON link.key_id = key.id AND link.tenant_id = key.tenant_id
            WHERE key.tenant_id = %s GROUP BY key.id ORDER BY key.created_at DESC LIMIT 100''', (user.tenant_id,))
    return {'items': [{**doc, 'id': str(doc['id'])} for doc in docs]}


@router.delete('/api-keys/{key_id}', status_code=204, tags=['API Keys'])
async def revoke(key_id: str, request: Request):
    user = await identity(request, 'keys:write')
    rt = request.app.state.rt
    async with rt.db.tx(user.tenant_id) as conn:
        result = await conn.execute('UPDATE whatsapp.api_keys SET revoked_at = now() WHERE id = %s AND tenant_id = %s',
                                    (as_uuid(key_id), user.tenant_id)) if as_uuid(key_id) else None
        if not result or not result.rowcount:
            fail(404, 'NOT_FOUND', 'Chave não encontrada.')
        await rt.audit(conn, user.tenant_id, user.actor_id, 'key.revoke', key_id)


@router.get('/dashboard', tags=['Painel'])
async def dashboard(request: Request):
    user = await identity(request, 'dashboard:read')
    rt = request.app.state.rt
    async with rt.db.tx(user.tenant_id) as conn:
        counts = await fetch_one(conn, '''
            SELECT count(*) AS total, count(*) FILTER (WHERE connection_state = 'connected') AS connected
            FROM whatsapp.instances WHERE tenant_id = %s''', (user.tenant_id,))
        stats = await fetch_one(conn, '''
            SELECT count(*) FILTER (WHERE status IN ('sent', 'delivered', 'read')) AS sent,
                   count(*) FILTER (WHERE status = 'received') AS received,
                   count(*) FILTER (WHERE status IN ('failed', 'unknown')) AS failed,
                   count(*) FILTER (WHERE status IN ('queued', 'processing', 'retry_wait')) AS queued
            FROM whatsapp.messages WHERE tenant_id = %s AND expires_at > now()''', (user.tenant_id,))
        events = await fetch_all(conn, 'SELECT action, created_at FROM whatsapp.audit_events WHERE tenant_id = %s '
                                 'ORDER BY created_at DESC LIMIT 10', (user.tenant_id,))
    heartbeat = await rt.redis.get('worker:heartbeat')
    return {'instances': counts['total'], 'connected': counts['connected'],
            'disconnected': counts['total'] - counts['connected'], **stats,
            'worker_online': bool(heartbeat), 'events': events}


@internal.post('/evolution/{instance_id}', include_in_schema=False)
async def webhook(instance_id: str, request: Request):
    rt = request.app.state.rt
    doc = None
    if as_uuid(instance_id):
        async with rt.db.tx() as conn:
            doc = await fetch_one(conn, 'SELECT * FROM whatsapp.webhook_instance(%s)', (as_uuid(instance_id),))
    supplied = request.headers.get('x-webhook-secret', '')
    if not doc or not doc['webhook_hash'] or not supplied or not hmac.compare_digest(rt.digest(supplied), doc['webhook_hash']):
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
    tenant, key = doc['tenant_id'], as_uuid(instance_id)
    try:
        async with rt.db.tx(tenant) as conn:
            for item in normalize_event(event, data):
                if event == 'connection.update':
                    # Cada reconexão precisa ser aplicada, mesmo com estado igual a uma conexão antiga.
                    item['occurrence'] = str(body.get('date_time') or new_id())[:100]
                event_key = rt.digest(json.dumps([str(key), event, item], sort_keys=True))
                # Sem alvo explícito: wa_api só tem INSERT nesta tabela; a PK (HMAC do evento) deduplica.
                await conn.execute('''
                    INSERT INTO whatsapp.gateway_events (event_key, tenant_id, instance_id, type, data, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING''',
                    (event_key, tenant, key, event, rt.seal(item), now() + timedelta(days=14)))
    except errors.ForeignKeyViolation:
        return {'status': 'ignored'}  # Instância excluída durante a entrega.
    return {'status': 'accepted'}
