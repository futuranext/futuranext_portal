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
from psycopg import Error as DatabaseError

from .core import fetch_all, fetch_one, new_id, now

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
    if str(UUID(tenant_id)) != tenant_id or str(UUID(instance_id)) != instance_id:
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
    return _validate_destination({'tenant_id': str(doc['tenant_id']), 'instance_id': str(doc['instance_id']),
        'kind': 'bot', 'url': doc['url'], 'secret': secret,
        'include_message': bool(doc.get('include_message', False))})


async def stored_destinations(rt):
    destinations = {}
    async with rt.db.tx() as conn:
        rows = await fetch_all(conn, 'SELECT * FROM whatsapp.callback_destinations WHERE enabled')
    for doc in rows:
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
            except DatabaseError:
                print('{"level":"error","event":"callback_registry_unavailable"}', flush=True)
        return self.value


async def enqueue_callbacks(conn, event, destinations):
    """Cria as entregas na mesma transação que marca o evento como processado."""
    event_type = EVENT_TYPES.get(event['type'])
    if not event_type:
        return
    tenant, instance = str(event['tenant_id']), str(event['instance_id'])
    for key, target in destinations.items():
        if (target.tenant_id, target.instance_id) != (tenant, instance):
            continue
        event_id = str(uuid5(NAMESPACE_URL, f'whatsapp:{tenant}:{instance}:{event["event_key"]}'))
        delivery_id = str(uuid5(NAMESPACE_URL, f'whatsapp-delivery:{event_id}:{key}'))
        await conn.execute('''
            INSERT INTO whatsapp.webhook_deliveries (id, tenant_id, instance_id, source_event, destination_key, kind,
                event_id, event_type, occurred_at, status, due_at, attempts, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', now(), 0, %s)
            ON CONFLICT (id) DO NOTHING''',
            (delivery_id, tenant, instance, event['event_key'], key, target.kind, event_id, event_type,
             event['created_at'], event['expires_at']))


async def _event_body(rt, delivery, target):
    async with rt.db.tx() as conn:
        source = await fetch_one(conn, '''
            SELECT data FROM whatsapp.gateway_events WHERE event_key = %s AND tenant_id = %s AND instance_id = %s
              AND expires_at > now()''', (delivery['source_event'], target.tenant_id, target.instance_id))
    if not source:
        return None
    body = {'id': str(delivery['event_id']), 'type': delivery['event_type'],
            'instance_id': str(delivery['instance_id']), 'occurred_at': delivery['occurred_at'].isoformat()}
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


async def recover_deliveries(rt):
    async with rt.db.tx() as conn:
        await conn.execute('''
            UPDATE whatsapp.webhook_deliveries SET status = 'retry_wait', due_at = now(),
                error_code = 'WORKER_INTERRUPTED', lease_owner = NULL, lease_until = NULL
            WHERE status = 'processing' AND lease_until < now() AND kind = 'futuranext' AND attempts <= %s''',
            (len(RETRY_SECONDS),))
        await conn.execute('''
            UPDATE whatsapp.webhook_deliveries SET status = 'unknown', error_code = 'WORKER_INTERRUPTED',
                lease_owner = NULL, lease_until = NULL
            WHERE status = 'processing' AND lease_until < now()''')


async def claim_delivery(rt, owner):
    async with rt.db.tx() as conn:
        return await fetch_one(conn, '''
            UPDATE whatsapp.webhook_deliveries SET status = 'processing', lease_owner = %s,
                lease_until = now() + make_interval(secs => %s), attempts = attempts + 1
            WHERE id = (
                SELECT id FROM whatsapp.webhook_deliveries
                WHERE status IN ('queued', 'retry_wait') AND due_at <= now() AND expires_at > now()
                ORDER BY due_at LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *''', (owner, LEASE_SECONDS))


async def dispatch_callback(rt, http, destinations):
    await recover_deliveries(rt)
    owner = new_id()
    delivery = await claim_delivery(rt, owner)
    if not delivery:
        return False

    tenant, instance = str(delivery['tenant_id']), str(delivery['instance_id'])
    target = destinations.get(delivery['destination_key'])
    async with rt.db.tx() as conn:
        available = await fetch_one(conn, '''
            SELECT whatsapp.tenant_licensed(%s) AS licensed, EXISTS (
                SELECT FROM whatsapp.instances WHERE id = %s AND tenant_id = %s AND connection_state <> 'deleting'
            ) AS instance''', (tenant, instance, tenant))
    if not target or (target.tenant_id, target.instance_id) != (tenant, instance):
        status, error, code = 'cancelled', 'DESTINATION_DISABLED', None
    elif not available['instance']:
        status, error, code = 'cancelled', 'INSTANCE_UNAVAILABLE', None
    elif not available['licensed']:
        status, error, code = 'cancelled', 'LICENSE_INACTIVE', None
    elif (body := await _event_body(rt, delivery, target)) is None:
        status, error, code = 'cancelled', 'EVENT_EXPIRED', None
    else:
        status, error, code = await _post_callback(http, target, body, str(delivery['event_id']))

    due_at = None
    if status == 'retry_wait':
        attempts = delivery['attempts']
        if attempts <= len(RETRY_SECONDS):
            due_at = now() + timedelta(seconds=RETRY_SECONDS[attempts - 1])
        else:
            status = 'unknown' if error == 'DELIVERY_UNCERTAIN' else 'failed'
    async with rt.db.tx() as conn:
        await conn.execute('''
            UPDATE whatsapp.webhook_deliveries SET status = %s, error_code = %s, http_status = %s,
                updated_at = now(), due_at = coalesce(%s, due_at), lease_owner = NULL, lease_until = NULL
            WHERE id = %s AND tenant_id = %s AND lease_owner = %s AND status = 'processing' ''',
            (status, error, code, due_at, delivery['id'], tenant, owner))
    return True


async def callback_loop(rt, registry):
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as http:
        while True:
            try:
                if not await dispatch_callback(rt, http, await registry.current()):
                    await asyncio.sleep(1)
            except (DatabaseError, InvalidToken, KeyError, ValueError):
                print('{"level":"error","event":"callback_iteration_failed"}', flush=True)
                await asyncio.sleep(3)
