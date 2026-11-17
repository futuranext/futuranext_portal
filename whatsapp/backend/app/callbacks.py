"""Cadastro por instância e entrega assinada de eventos ao código dos bots."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from cryptography.fernet import InvalidToken
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from .core import new_id, now

EVENT_TYPES = {
    'messages.upsert': 'message.received',
    'messages.update': 'message.status',
    'connection.update': 'instance.connection',
}
RETRY_SECONDS = (5, 30, 120)
LEASE_SECONDS = 45
HOST_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.ASCII)
PORTAL_PATH = re.compile(r'/api/v1/integrations/whatsapp/([0-9a-f-]{36})/events', re.ASCII)


@dataclass(frozen=True)
class Destination:
    tenant_id: str
    instance_id: str
    kind: str
    url: str
    hostname: str
    include_message: bool
    secret: str = field(repr=False)

    @property
    def key(self):
        identity = [self.tenant_id, self.instance_id, self.kind, self.url, self.include_message]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _validate_destination(item):
    required = {'tenant_id', 'instance_id', 'kind', 'url', 'secret'}
    if not isinstance(item, dict) or not required <= item.keys() <= required | {'include_message'}:
        raise ValueError
    if any(not isinstance(item[key], str) for key in required):
        raise ValueError
    tenant_id, instance_id, kind, url, secret = (item[key] for key in
        ('tenant_id', 'instance_id', 'kind', 'url', 'secret'))
    if (tenant_id != 'local-company' and str(UUID(tenant_id)) != tenant_id) or str(UUID(instance_id)) != instance_id:
        raise ValueError
    include_message = item.get('include_message', False)
    if kind not in ('bot', 'futuranext') or type(include_message) is not bool:
        raise ValueError
    if len(secret) < 32 or len(secret) > 512 or len(url) > 2048:
        raise ValueError
    if any(not 33 <= ord(char) <= 126 for char in url) or any(char in url for char in '\\?#'):
        raise ValueError
    parts = urlsplit(url)
    hostname = parts.hostname or ''
    labels = hostname.split('.')
    if (parts.scheme != 'https' or parts.netloc != hostname or len(hostname) > 253
            or len(labels) < 2 or not labels[-1][0:1].isalpha()
            or any(not HOST_LABEL.fullmatch(label) for label in labels)
            or not parts.path.startswith('/')):
        raise ValueError
    if kind == 'futuranext':
        match = PORTAL_PATH.fullmatch(parts.path)
        if include_message or not match or str(UUID(match[1])) != match[1]:
            raise ValueError
    return Destination(tenant_id, instance_id, kind, url, hostname, include_message, secret)


def load_destinations(raw):
    if not raw or not raw.strip():
        return {}
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            raise TypeError
        destinations = {}
        instances = {}
        secrets = set()
        endpoints = set()
        for item in items:
            target = _validate_destination(item)
            endpoint = (target.instance_id, target.url)
            tenant = instances.setdefault(target.instance_id, target.tenant_id)
            if tenant != target.tenant_id or target.secret in secrets or endpoint in endpoints:
                raise ValueError
            secrets.add(target.secret)
            endpoints.add(endpoint)
            destinations[target.key] = target
        return destinations
    except (ValueError, TypeError):
        raise ValueError('Configuração WHATSAPP_OUTBOUND_WEBHOOKS inválida.') from None


REFRESH_SECONDS = 10


def bot_destination(doc, secret):
    """Valida um destino cadastrado pelo portal com as mesmas regras do cadastro do operador."""
    return _validate_destination({'tenant_id': doc['tenant_id'], 'instance_id': doc['instance_id'],
        'kind': 'bot', 'url': doc['url'], 'secret': secret,
        'include_message': bool(doc.get('include_message', False))})


async def stored_destinations(rt):
    destinations = {}
    async for doc in rt.db.callback_destinations.find({'enabled': True}):
        try:
            target = bot_destination(doc, rt.unseal(doc['secret']))
        except (ValueError, KeyError, TypeError, InvalidToken):
            print('{"level":"error","event":"callback_destination_invalid"}', flush=True)
            continue
        destinations[target.key] = target
    return destinations


class DestinationRegistry:
    """Une o cadastro fixo do operador aos destinos que cada empresa cadastra no portal."""

    def __init__(self, rt, static):
        self.rt, self.static = rt, dict(static)
        self.value, self.loaded = dict(static), float('-inf')

    async def current(self):
        if time.monotonic() - self.loaded >= REFRESH_SECONDS:
            try:
                self.value = {**await stored_destinations(self.rt), **self.static}
                self.loaded = time.monotonic()
            except PyMongoError:
                print('{"level":"error","event":"callback_registry_unavailable"}', flush=True)
        return self.value


async def enqueue_callbacks(rt, event, destinations):
    event_type = EVENT_TYPES.get(event['type'])
    if not event_type:
        return
    tenant, instance = event['tenant_id'], event['instance_id']
    for key, target in destinations.items():
        if (target.tenant_id, target.instance_id) != (tenant, instance):
            continue
        event_id = str(uuid5(NAMESPACE_URL, f'whatsapp:{tenant}:{instance}:{event["_id"]}'))
        delivery_id = str(uuid5(NAMESPACE_URL, f'whatsapp-delivery:{event_id}:{key}'))
        query = {'_id': delivery_id, 'tenant_id': tenant, 'instance_id': instance}
        try:
            await rt.db.webhook_deliveries.update_one(query, {'$setOnInsert': {
                'destination_key': key, 'kind': target.kind, 'source_id': event['_id'],
                'event_id': event_id, 'event_type': event_type, 'occurred_at': event['created_at'],
                'status': 'queued', 'due_at': now(), 'attempts': 0, 'expires_at': event['expires_at'],
            }}, upsert=True)
        except DuplicateKeyError:
            if not await rt.db.webhook_deliveries.find_one(query):
                raise


async def _event_body(rt, delivery, target):
    scope = {'_id': delivery['source_id'], 'tenant_id': target.tenant_id, 'instance_id': target.instance_id}
    source = await rt.db.gateway_events.find_one({**scope, 'expires_at': {'$gt': now()}})
    if not source:
        return None
    body = {'id': delivery['event_id'], 'type': delivery['event_type'],
            'instance_id': delivery['instance_id'], 'occurred_at': delivery['occurred_at'].isoformat()}
    if target.kind == 'bot':
        field_names = {
            'message.received': ('phone', 'message', 'message_id') if target.include_message else (),
            'message.status': ('status', 'message_id'),
            'instance.connection': ('state',),
        }
        fields = field_names[delivery['event_type']]
        data = rt.unseal(source['data']) if fields else {}
        body['data'] = {key: data[key] for key in fields if key in data}
    return json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _is_public(address):
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_loopback and not address.is_link_local
            and not (address.version == 6 and (address.ipv4_mapped or address.sixtofour or address.teredo)))


async def _resolve(hostname):
    async with asyncio.timeout(3):
        answers = await asyncio.get_running_loop().getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(ipaddress.ip_address(answer[4][0]) for answer in answers))
    if not addresses or any(not _is_public(address) for address in addresses):
        raise ValueError('Destino de webhook sem resolução pública.')
    return min(addresses, key=lambda address: address.version).compressed


async def _post_callback(http, target, body, event_id):
    try:
        address = await _resolve(target.hostname)
    except ValueError:
        return 'failed', 'DESTINATION_NOT_PUBLIC', None
    except (OSError, TimeoutError):
        return 'retry_wait', 'DNS_FAILURE', None
    timestamp = str(int(now().timestamp()))
    digest = hmac.new(target.secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    headers = {'Host': target.hostname, 'Content-Type': 'application/json',
               'X-Event-Id': event_id, 'X-Timestamp': timestamp, 'X-Signature': 'sha256=' + digest}
    http.cookies.clear()
    try:
        async with asyncio.timeout(10):
            async with http.stream('POST', httpx.URL(target.url).copy_with(host=address), content=body,
                                   headers=headers, extensions={'sni_hostname': target.hostname}) as response:
                code = response.status_code
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return 'retry_wait', 'CONNECTION_FAILURE', None
    except (httpx.TransportError, TimeoutError):
        status = 'retry_wait' if target.kind == 'futuranext' else 'unknown'
        return status, 'DELIVERY_UNCERTAIN', None
    if 200 <= code < 300:
        return 'delivered', None, code
    if code == 429 or (target.kind == 'futuranext' and (code == 408 or code >= 500)):
        return 'retry_wait', 'HTTP_RETRYABLE', code
    if code >= 500 or code == 408:
        return 'unknown', 'DELIVERY_UNCERTAIN', code
    return 'failed', 'HTTP_REJECTED', code


async def dispatch_callback(rt, http, destinations):
    deliveries = rt.db.webhook_deliveries
    timestamp = now()
    expired = {'status': 'processing', 'lease_until': {'$lt': timestamp}}
    await deliveries.update_many({**expired, 'kind': 'futuranext', 'attempts': {'$lte': len(RETRY_SECONDS)}},
        {'$set': {'status': 'retry_wait', 'due_at': timestamp, 'error_code': 'WORKER_INTERRUPTED'}})
    await deliveries.update_many(expired, {'$set': {'status': 'unknown', 'error_code': 'WORKER_INTERRUPTED'}})
    owner = new_id()
    delivery = await deliveries.find_one_and_update(
        {'status': {'$in': ['queued', 'retry_wait']}, 'due_at': {'$lte': timestamp}, 'expires_at': {'$gt': timestamp}},
        {'$set': {'status': 'processing', 'lease_owner': owner,
                  'lease_until': timestamp + timedelta(seconds=LEASE_SECONDS)}, '$inc': {'attempts': 1}},
        sort=[('due_at', 1)], return_document=ReturnDocument.AFTER)
    if not delivery:
        return False

    scope = {key: delivery[key] for key in ('_id', 'tenant_id', 'instance_id')}
    query = {**scope, 'lease_owner': owner, 'status': 'processing'}
    target = destinations.get(delivery['destination_key'])
    if not target or (target.tenant_id, target.instance_id) != (delivery['tenant_id'], delivery['instance_id']):
        status, error, code = 'cancelled', 'DESTINATION_DISABLED', None
    elif not await rt.db.instances.find_one({'_id': target.instance_id, 'tenant_id': target.tenant_id,
                                             'connection_state': {'$ne': 'deleting'}}):
        status, error, code = 'cancelled', 'INSTANCE_UNAVAILABLE', None
    elif (body := await _event_body(rt, delivery, target)) is None:
        status, error, code = 'cancelled', 'EVENT_EXPIRED', None
    else:
        status, error, code = await _post_callback(http, target, body, delivery['event_id'])

    changes = {'status': status, 'error_code': error, 'http_status': code, 'updated_at': now()}
    if status == 'retry_wait':
        attempts = delivery['attempts']
        if attempts <= len(RETRY_SECONDS):
            changes['due_at'] = now() + timedelta(seconds=RETRY_SECONDS[attempts - 1])
        else:
            changes['status'] = 'unknown' if error == 'DELIVERY_UNCERTAIN' else 'failed'
    await deliveries.update_one(query, {'$set': changes, '$unset': {'lease_owner': '', 'lease_until': ''}})
    return True


async def callback_loop(rt, registry):
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as http:
        while True:
            try:
                if not await dispatch_callback(rt, http, await registry.current()):
                    await asyncio.sleep(1)
            except (PyMongoError, InvalidToken, KeyError, ValueError):
                print('{"level":"error","event":"callback_iteration_failed"}', flush=True)
                await asyncio.sleep(3)
