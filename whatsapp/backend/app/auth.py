"""Sessões opacas, CSRF e chaves de integração com escopos e tenant obrigatório."""
import asyncio
import hmac
import secrets
from datetime import timedelta
from dataclasses import dataclass
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .core import fail, now

router = APIRouter(prefix='/api/v1/auth', tags=['Autenticação'])
passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


@dataclass
class Identity:
    tenant_id: str
    actor_id: str
    scopes: list[str]
    instance_ids: list[str] | None
    session: bool = False
    csrf: str = ''
    global_admin: bool = False
    owner_tenant: str = ''


def origin_check(request: Request):
    allowed = request.app.state.rt.config.frontend_origins.split(',')
    if request.headers.get('origin') not in allowed:
        fail(403, 'ORIGIN_DENIED', 'Origem não permitida.')


async def identity(request: Request, scope: str = '') -> Identity:
    rt = request.app.state.rt
    auth = request.headers.get('authorization', '')
    token = request.cookies.get('wp_session')
    if auth and token:
        fail(400, 'AMBIGUOUS_AUTH', 'Use apenas um método de autenticação.')
    if auth.startswith('Bearer '):
        doc = await rt.db.api_keys.find_one({'secret_hash': rt.digest(auth[7:]),
            'revoked_at': None, 'expires_at': {'$gt': now()}})
        if not doc:
            fail(401, 'UNAUTHORIZED', 'Chave inválida ou expirada.')
        user = Identity(doc['tenant_id'], doc['_id'], doc['scopes'], doc['instance_ids'])
    elif token:
        doc = await rt.db.sessions.find_one({'_id': rt.digest(token), 'expires_at': {'$gt': now()}})
        if not doc:
            fail(401, 'UNAUTHORIZED', 'Sessão expirada. Entre novamente.')
        account = await rt.db.users.find_one({'_id': doc['user_id'], 'tenant_id': doc['tenant_id'], 'disabled': False})
        if not account:
            fail(401, 'UNAUTHORIZED', 'Conta indisponível.')
        if doc.get('auth_version', 0) != account.get('auth_version', 0):
            fail(401, 'UNAUTHORIZED', 'Sua senha foi alterada. Entre novamente.')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin_check(request)
            if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), doc['csrf']):
                fail(403, 'CSRF', 'Atualize a página e tente novamente.')
        scopes = ['*'] if account['role'] in ('global_admin', 'tenant_admin') else ['instances:read', 'messages:read', 'dashboard:read']
        user = Identity(doc['tenant_id'], account['_id'], scopes, None, True, doc['csrf'],
                        account['role'] == 'global_admin', doc['tenant_id'])
    else:
        fail(401, 'UNAUTHORIZED', 'Entre no painel ou informe sua API Key.')
    if scope and '*' not in user.scopes and scope not in user.scopes:
        fail(403, 'FORBIDDEN', 'Permissão insuficiente para esta ação.')
    selected = request.headers.get('x-tenant-id')
    if selected:
        if not user.global_admin:
            fail(403, 'FORBIDDEN', 'Somente o administrador global pode selecionar outra conta.')
        if not await rt.db.tenants.find_one({'_id': selected}):
            fail(404, 'NOT_FOUND', 'Conta não encontrada.')
        user.tenant_id = selected
        if selected != user.owner_tenant:
            await rt.audit(selected, user.actor_id, 'admin.account_access')
    await rt.limit(f'actor:{user.actor_id}', 120)
    return user


def tenant_filter(user: Identity, instance_id: str | None = None):
    query = {'tenant_id': user.tenant_id}
    if instance_id:
        if user.instance_ids is not None and instance_id not in user.instance_ids:
            fail(404, 'NOT_FOUND', 'Instância não encontrada.')
        query['_id'] = instance_id
    elif user.instance_ids is not None:
        query['_id'] = {'$in': user.instance_ids}
    return query


@router.get('/setup-status')
async def setup_status(request: Request):
    return {'configured': await request.app.state.rt.db.users.count_documents({}) > 0}


@router.post('/login')
async def login(body: Login, request: Request, response: Response):
    origin_check(request)
    rt = request.app.state.rt
    email = body.email.strip().lower()
    await rt.limit(f'login-ip:{request.client.host}', 10, 300)
    await rt.limit(f'login-account:{rt.digest(email)}', 10, 300)
    account = await rt.db.users.find_one({'email': email, 'disabled': False})
    try:
        valid = await asyncio.to_thread(passwords.verify, account['password_hash'] if account else DUMMY_HASH, body.password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or not account:
        fail(401, 'LOGIN_FAILED', 'E-mail ou senha inválidos.')
    old = request.cookies.get('wp_session')
    if old:
        await rt.db.sessions.delete_one({'_id': rt.digest(old)})
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await rt.db.sessions.insert_one({'_id': rt.digest(token), 'user_id': account['_id'],
        'tenant_id': account['tenant_id'], 'csrf': csrf, 'expires_at': now() + timedelta(hours=8),
        'auth_version': account.get('auth_version', 0)})
    response.set_cookie('wp_session', token, httponly=True, secure=rt.config.cookie_secure,
                         samesite='lax', max_age=28800, path='/')
    await rt.audit(account['tenant_id'], account['_id'], 'auth.login')
    return {'csrf_token': csrf}


@router.get('/me')
async def me(request: Request):
    user = await identity(request)
    if not user.session:
        fail(403, 'SESSION_REQUIRED', 'Esta operação exige login no painel.')
    account = await request.app.state.rt.db.users.find_one({'_id': user.actor_id, 'tenant_id': user.owner_tenant})
    company = await request.app.state.rt.db.tenants.find_one({'_id': user.tenant_id})
    return {'email': account['email'], 'company': company['name'], 'role': account['role'], 'csrf_token': user.csrf,
            'tenant_id': user.tenant_id, 'owner_tenant_id': user.owner_tenant}


@router.post('/logout', status_code=204)
async def logout(request: Request, response: Response):
    user = await identity(request)
    if user.session:
        await request.app.state.rt.db.sessions.delete_one({'_id': request.app.state.rt.digest(request.cookies['wp_session'])})
        await request.app.state.rt.audit(user.tenant_id, user.actor_id, 'auth.logout')
    response.delete_cookie('wp_session', path='/')
