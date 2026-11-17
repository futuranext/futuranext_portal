"""Cadastro após confirmação do e-mail e recuperação com token de uso único."""
import asyncio
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from .auth import origin_check, passwords, identity
from .core import fail, new_id, now
from . import email_service

router = APIRouter(prefix='/api/v1', tags=['Contas'])
GENERIC = {'message': 'Se o endereço estiver apto, você receberá um e-mail com as instruções. Confira também o spam.'}


class EmailInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    email: str = Field(max_length=254, pattern=email_service.EMAIL)


class Register(EmailInput):
    company: str = Field(min_length=2, max_length=100)


class Complete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    password: str = Field(min_length=12, max_length=256)


async def limit(request, email=None):
    origin_check(request)
    rt = request.app.state.rt
    await rt.limit(f'account-public:{request.client.host}', 20, 3600)
    if email:
        await rt.limit(f'account-email:{rt.digest(email)}', 3, 3600)
    return rt


async def deliver(rt, kind, recipient, token, ref):
    registration = kind == 'register'
    title = 'Confirme seu cadastro' if registration else 'Redefina sua senha'
    url = email_service.site_url(rt.config) + ('/cadastro' if registration else '/redefinir-senha') + '#' + token
    explanation = ('Confirme seu endereço de e-mail e defina uma senha para criar sua conta.' if registration
                   else 'Recebemos uma solicitação para alterar a senha da sua conta.')
    footer = 'Este link vale por 30 minutos. Se não foi você, ignore este e-mail. Não encaminhe o link.'
    return await email_service.send_email(rt.config, recipient=recipient, subject=title,
        html=email_service.link_message(title, explanation, url, title, footer),
        text=f'{title}\n{explanation}\n{url}\n{footer}', reference=ref)


@router.post('/auth/register', status_code=202)
async def register(body: Register, request: Request):
    email = body.email.lower()
    rt = await limit(request, email)
    email_service.require_email(rt.config)
    # Não expõe se a conta já existe e nunca substitui uma conta existente.
    if await rt.db.users.find_one({'email': email}):
        return GENERIC
    token, rid, tid = secrets.token_urlsafe(32), new_id(), new_id()
    doc = {'_id': rid, 'tenant_id': tid, 'email_hash': rt.digest(email),
           'token_hash': rt.digest('register:' + token), 'created_at': now(),
           'expires_at': now() + timedelta(minutes=30), 'used': False,
           'content': rt.seal({'email': email, 'company': body.company}), 'email_status': 'sending'}
    await rt.db.account_requests.insert_one(doc)
    status = await deliver(rt, 'register', email, token, rid)
    await rt.db.account_requests.update_one({'_id': rid}, {'$set': {'email_status': status}})
    return GENERIC


@router.post('/auth/complete-registration')
async def complete_registration(body: Complete, request: Request):
    rt = await limit(request)
    doc = await rt.db.account_requests.find_one({'token_hash': rt.digest('register:' + body.token),
        'expires_at': {'$gt': now()}, 'used': False, 'email_status': {'$in': ['sending', 'accepted', 'unknown']}})
    if not doc:
        fail(400, 'INVALID_LINK', 'Link inválido, utilizado ou expirado. Solicite um novo cadastro.')
    data = rt.unseal(doc['content'])
    password_hash = await asyncio.to_thread(passwords.hash, body.password)
    # IDs estáveis por solicitação permitem reaplicar após interrupção sem duplicar conta.
    await rt.db.tenants.update_one({'_id': doc['tenant_id']}, {'$setOnInsert': {
        'name': data['company'], 'created_at': now(), 'signup_request': doc['_id']}}, upsert=True)
    try:
        await rt.db.users.insert_one({'_id': doc['_id'], 'tenant_id': doc['tenant_id'], 'email': data['email'],
            'password_hash': password_hash, 'role': 'tenant_admin', 'disabled': False,
            'email_verified_at': now(), 'auth_version': 0, 'created_at': now()})
    except DuplicateKeyError:
        # Nenhuma senha/permissão é sobrescrita, inclusive em duas confirmações concorrentes.
        if not await rt.db.users.find_one({'tenant_id': doc['tenant_id']}):
            await rt.db.tenants.delete_one({'_id': doc['tenant_id'], 'signup_request': doc['_id']})
        fail(409, 'ACCOUNT_EXISTS', 'A conta já foi criada. Entre ou utilize Esqueceu a senha.')
    await rt.db.account_requests.update_one({'_id': doc['_id']}, {'$set': {'used': True}})
    await rt.audit(doc['tenant_id'], doc['_id'], 'account.register')
    return {'status': 'created'}


@router.post('/auth/forgot-password', status_code=202)
async def forgot(body: EmailInput, request: Request):
    email = body.email.lower()
    rt = await limit(request, email)
    email_service.require_email(rt.config)
    account = await rt.db.users.find_one({'email': email, 'disabled': False})
    if not account:
        return GENERIC
    token = secrets.token_urlsafe(32)
    digest = rt.digest('reset:' + token)
    await rt.db.users.update_one({'_id': account['_id'], 'tenant_id': account['tenant_id']},
        {'$set': {'reset_token_hash': digest, 'reset_expires_at': now() + timedelta(minutes=30)}})
    status = await deliver(rt, 'reset', email, token, new_id())
    if status == 'failed':
        await rt.db.users.update_one({'_id': account['_id'], 'reset_token_hash': digest},
            {'$unset': {'reset_token_hash': '', 'reset_expires_at': ''}})
    await rt.audit(account['tenant_id'], account['_id'], 'account.reset_requested')
    return GENERIC


@router.post('/auth/reset-password')
async def reset(body: Complete, request: Request):
    rt = await limit(request)
    digest = rt.digest('reset:' + body.token)
    account = await rt.db.users.find_one({'reset_token_hash': digest, 'reset_expires_at': {'$gt': now()}, 'disabled': False})
    if not account:
        fail(400, 'INVALID_LINK', 'Link inválido, utilizado ou expirado. Solicite outro e-mail.')
    password_hash = await asyncio.to_thread(passwords.hash, body.password)
    result = await rt.db.users.update_one({'_id': account['_id'], 'tenant_id': account['tenant_id'],
        'reset_token_hash': digest, 'reset_expires_at': {'$gt': now()}, 'disabled': False},
        {'$set': {'password_hash': password_hash}, '$inc': {'auth_version': 1},
         '$unset': {'reset_token_hash': '', 'reset_expires_at': ''}})
    if not result.modified_count:
        fail(400, 'INVALID_LINK', 'Link já utilizado ou expirado.')
    await rt.db.sessions.delete_many({'user_id': account['_id'], 'tenant_id': account['tenant_id']})
    await rt.audit(account['tenant_id'], account['_id'], 'account.password_reset')
    return {'status': 'password_updated'}


@router.get('/admin/accounts')
async def admin_accounts(request: Request, page: int = 1):
    user = await identity(request)
    if not user.global_admin:
        fail(403, 'FORBIDDEN', 'Acesso exclusivo do administrador da plataforma.')
    if not 1 <= page <= 10000:
        fail(422, 'INVALID_PAGE', 'Página inválida.')
    rt = request.app.state.rt
    tenants = await rt.db.tenants.find({}).sort('_id', 1).skip((page - 1) * 50).to_list(50)
    items = []
    for tenant in tenants:
        accounts = await rt.db.users.find({'tenant_id': tenant['_id']}, {'email': 1, 'role': 1, 'disabled': 1}).to_list(100)
        items.append({'id': tenant['_id'], 'company': tenant['name'],
            'users': [{'email': a['email'], 'role': a['role'], 'disabled': a['disabled']} for a in accounts],
            'instances': await rt.db.instances.count_documents({'tenant_id': tenant['_id']})})
    await rt.audit(user.owner_tenant, user.actor_id, 'admin.accounts_view')
    return {'items': items, 'page': page, 'has_more': await rt.db.tenants.count_documents({}) > page * 50}
