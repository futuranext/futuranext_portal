"""Autorização explícita por link: consulta nunca concede permissão."""
from datetime import timedelta
import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo import ReturnDocument

from .auth import identity
from .core import fail, new_id, now
from . import email_service

router = APIRouter(prefix='/api/v1', tags=['Autorizações por link'])


class Invite(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    phone: str = Field(pattern=r'^[1-9][0-9]{7,14}$')
    email: str = Field(max_length=254, pattern=r'^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$')
    purpose: str = Field(min_length=10, max_length=300)


class Token(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


class Confirmation(Token):
    accepted: StrictBool
    statement_version: str = Field(pattern=r'^1$')


def base_url(rt):
    return email_service.site_url(rt.config)


def email_ready(rt):
    return email_service.email_ready(rt.config)


def public_record(rt, doc):
    data = rt.unseal(doc['content'])
    status = doc['status']
    if status == 'pending' and doc['expires_at'] <= now():
        status = 'expired'
    if status == 'confirmed' and doc.get('delivery_channel') != 'email_zoho':
        status = 'legacy'
    return {'id': doc['_id'], 'phone': data['phone'], 'email': data['email'],
            'purpose': data['purpose'], 'status': status, 'email_status': doc['email_status'],
            'created_at': doc['created_at'], 'expires_at': doc['expires_at'],
            'confirmed_at': doc.get('confirmed_at')}


@router.get('/consent-requests')
async def list_requests(request: Request):
    user = await identity(request, 'consents:write')
    rt = request.app.state.rt
    docs = await rt.db.consent_requests.find({'tenant_id': user.tenant_id}).sort('created_at', -1).to_list(100)
    return {'items': [public_record(rt, doc) for doc in docs], 'email_ready': email_ready(rt),
            'base_url': base_url(rt)}


@router.post('/consent-requests', status_code=201)
async def create_request(body: Invite, request: Request):
    user = await identity(request, 'consents:write')
    rt = request.app.state.rt
    email_service.require_email(rt.config)
    await rt.limit(f'consent-create:{user.tenant_id}', 30, 3600)
    phone_hash = rt.digest(f'{user.tenant_id}:{body.phone}')
    if await rt.db.suppressions.find_one({'tenant_id': user.tenant_id, 'phone_hash': phone_hash}):
        fail(409, 'OPTED_OUT', 'Contato bloqueado por opt-out. Este fluxo não remove o bloqueio.')
    tenant = await rt.db.tenants.find_one({'_id': user.tenant_id})
    token = secrets.token_urlsafe(32)
    stamp = now()
    company = tenant['name']
    statement = (f'Autorizo {company} a enviar mensagens pelo WhatsApp para {body.phone} '
                 f'com a finalidade: {body.purpose}. Posso pedir para parar a qualquer momento, respondendo SAIR.')
    doc = {'_id': new_id(), 'tenant_id': user.tenant_id, 'phone_hash': phone_hash,
           'token_hash': rt.digest('consent:' + token), 'status': 'pending', 'email_status': 'not_sent',
           'created_at': stamp, 'expires_at': stamp + timedelta(hours=48),
           'purge_at': stamp + timedelta(days=30), 'statement_version': '1',
           'content': rt.seal({**body.model_dump(), 'company': company, 'statement': statement}),
           'token_encrypted': rt.seal(token)}
    await rt.db.consent_requests.insert_one(doc)
    await rt.audit(user.tenant_id, user.actor_id, 'consent.request', doc['_id'])
    await email_request(doc['_id'], request)
    saved = await rt.db.consent_requests.find_one({'_id': doc['_id'], 'tenant_id': user.tenant_id})
    return public_record(rt, saved)


@router.delete('/consent-requests/{request_id}', status_code=204)
async def cancel_request(request_id: str, request: Request):
    user = await identity(request, 'consents:write')
    rt = request.app.state.rt
    result = await rt.db.consent_requests.update_one(
        {'_id': request_id, 'tenant_id': user.tenant_id, 'status': 'pending'},
        {'$set': {'status': 'cancelled'}, '$unset': {'token_encrypted': ''}})
    if not result.modified_count:
        fail(409, 'NOT_PENDING', 'Solicitação indisponível ou já finalizada. Para bloquear contato autorizado, use opt-out.')
    await rt.audit(user.tenant_id, user.actor_id, 'consent.cancel', request_id)


@router.post('/consent-requests/{request_id}/email')
async def email_request(request_id: str, request: Request):
    user = await identity(request, 'consents:write')
    rt = request.app.state.rt
    if not email_ready(rt):
        fail(409, 'EMAIL_NOT_CONFIGURED', 'Configure Zoho e uma URL HTTPS pública antes de enviar aos clientes.')
    await rt.limit(f'consent-email:{user.tenant_id}', 20, 3600)
    doc = await rt.db.consent_requests.find_one_and_update(
        {'_id': request_id, 'tenant_id': user.tenant_id, 'status': 'pending',
         'expires_at': {'$gt': now()}, 'email_status': 'not_sent'},
        {'$set': {'email_status': 'sending', 'delivery_channel': 'email_zoho', 'email_attempted_at': now()}}, return_document=ReturnDocument.AFTER)
    if not doc:
        fail(409, 'EMAIL_ALREADY_ATTEMPTED', 'Solicitação indisponível ou envio já tentado. Confira o histórico.')
    data = rt.unseal(doc['content'])
    url = base_url(rt) + '/autorizar#' + rt.unseal(doc['token_encrypted'])
    explanation = f"{data['company']} solicita autorização para contatar {data['phone']} pelo WhatsApp. Finalidade: {data['purpose']}"
    footer = 'Link válido por 48 horas. Abrir não autoriza: marque a caixa e confirme se concordar. Não encaminhe. Para parar, responda SAIR no WhatsApp.'
    status = await email_service.send_email(rt.config, recipient=data['email'],
        subject='Confirme sua autorização para mensagens pelo WhatsApp',
        html=email_service.link_message('Autorizar mensagens', explanation, url, 'Revisar autorização', footer),
        text=f'{explanation}\n{url}\n{footer}', reference=doc['_id'])
    await rt.db.consent_requests.update_one({'_id': doc['_id'], 'tenant_id': user.tenant_id},
        {'$set': {'email_status': status, 'email_attempted_at': now()}})
    await rt.audit(user.tenant_id, user.actor_id, 'consent.email.' + status, doc['_id'])
    return {'email_status': status}


async def resolve(body, request):
    rt = request.app.state.rt
    origin = request.headers.get('origin')
    if origin != base_url(rt):
        fail(403, 'ORIGIN_FORBIDDEN', 'Abra o link no endereço correto da plataforma.')
    await rt.limit(f'consent-public:{request.client.host}', 120, 60)
    doc = await rt.db.consent_requests.find_one({'token_hash': rt.digest('consent:' + body.token)})
    if (not doc or doc['status'] == 'cancelled' or doc.get('delivery_channel') != 'email_zoho'
            or doc.get('email_status') not in ('sending', 'accepted', 'unknown')):
        fail(404, 'LINK_UNAVAILABLE', 'Link inválido ou cancelado. Solicite um novo link à empresa.')
    query = {'tenant_id': doc['tenant_id'], 'phone_hash': doc['phone_hash']}
    if await rt.db.suppressions.find_one(query):
        fail(409, 'OPTED_OUT', 'Este contato está bloqueado. O link não remove um pedido para parar.')
    if doc['expires_at'] <= now():
        fail(410, 'LINK_EXPIRED', 'Este link expirou. Solicite um novo link à empresa.')
    return rt, doc


@router.post('/public/consent/details')
async def details(body: Token, request: Request):
    rt, doc = await resolve(body, request)
    data = rt.unseal(doc['content'])
    return {'company': data['company'], 'phone': data['phone'], 'purpose': data['purpose'],
            'statement': data['statement'], 'statement_version': doc['statement_version'],
            'status': doc['status'], 'expires_at': doc['expires_at']}


@router.post('/public/consent/confirm')
async def confirm(body: Confirmation, request: Request):
    if not body.accepted:
        fail(422, 'ACCEPTANCE_REQUIRED', 'Marque a caixa apenas se concordar com a autorização.')
    rt, doc = await resolve(body, request)
    if doc['status'] == 'confirmed':
        return {'status': 'confirmed', 'confirmed_at': doc['confirmed_at']}
    stamp = now()
    # A autorização e sua evidência passam a existir na mesma escrita atômica.
    result = await rt.db.consent_requests.find_one_and_update(
        {'_id': doc['_id'], 'tenant_id': doc['tenant_id'], 'status': 'pending', 'expires_at': {'$gt': stamp}},
        {'$set': {'status': 'confirmed', 'confirmed_at': stamp, 'accepted': True},
         '$unset': {'token_encrypted': '', 'purge_at': ''}}, return_document=ReturnDocument.AFTER)
    if not result:
        latest = await rt.db.consent_requests.find_one({'_id': doc['_id'], 'status': 'confirmed'})
        if latest:
            return {'status': 'confirmed', 'confirmed_at': latest['confirmed_at']}
        fail(409, 'LINK_UNAVAILABLE', 'A solicitação foi cancelada ou expirou. Solicite outro link.')
    return {'status': 'confirmed', 'confirmed_at': result['confirmed_at']}
