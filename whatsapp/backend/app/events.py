"""Normalização e aplicação idempotente de eventos do gateway."""
import re
from datetime import timedelta
from pymongo.errors import DuplicateKeyError
from .core import now, new_id
from .providers import connection_state

RANKS = {'sent': 1, 'delivered': 2, 'read': 3}


def normalize_event(event, data):
    result = []
    for row in data if isinstance(data, list) else [data]:
        if not isinstance(row, dict):
            continue
        if event == 'connection.update':
            result.append({'state': connection_state(row.get('state')), 'wuid': str(row.get('wuid', ''))[:80]})
        elif event == 'messages.update':
            key = row.get('key') or {}
            message_id = row.get('keyId') or key.get('id')
            state = row.get('status', (row.get('update') or {}).get('status'))
            state = {1: 'PENDING', 2: 'SERVER_ACK', 3: 'DELIVERY_ACK', 4: 'READ', 5: 'PLAYED'}.get(state, state)
            mapped = {'SERVER_ACK': 'sent', 'DELIVERY_ACK': 'delivered', 'READ': 'read', 'PLAYED': 'read'}.get(state)
            if message_id and mapped:
                result.append({'message_id': str(message_id)[:200], 'status': mapped})
        else:
            key = row.get('key') or {}
            if key.get('fromMe') or not key.get('id'):
                continue
            jid = key.get('remoteJidAlt') or key.get('remoteJid', '')
            if not isinstance(jid, str) or not jid.endswith('@s.whatsapp.net'):
                continue
            phone = jid.split('@')[0]
            if not re.fullmatch(r'[1-9][0-9]{7,14}', phone):
                continue
            message = row.get('message') or {}
            text = message.get('conversation') or (message.get('extendedTextMessage') or {}).get('text')
            result.append({'message_id': str(key['id'])[:200], 'phone': phone,
                           'message': str(text or '[Mídia recebida; download não disponível no MVP]')[:4096]})
    return result


async def process_event(rt, event):
    data = rt.unseal(event['data'])
    tenant, iid = event['tenant_id'], event['instance_id']
    instance = await rt.db.instances.find_one({'_id': iid, 'tenant_id': tenant})
    if not instance:
        return True
    if event['type'] == 'connection.update':
        state = data['state']
        update = {'connection_state': state, 'observed_at': now()}
        if state in ('connected', 'disconnected') and state != instance['connection_state']:
            update['last_connected_at' if state == 'connected' else 'last_disconnected_at'] = now()
            await rt.audit(tenant, 'gateway', 'instance.connection.' + state, iid)
        phone = data.get('wuid', '').split('@')[0]
        if phone:
            update['phone_masked'] = '•••• ' + phone[-4:]
            update['connected_phone'] = rt.seal(phone)
        await rt.db.instances.update_one({'_id': iid, 'tenant_id': tenant,
            'connection_state': {'$ne': 'deleting'}, '$or': [{'last_event_at': {'$lt': event['created_at']}}, {'last_event_at': {'$exists': False}}]},
            {'$set': {**update, 'last_event_at': event['created_at']}})
        if state == 'connected':
            await rt.redis.delete(f'qr:{iid}')
    elif event['type'] == 'messages.update':
        query = {'tenant_id': tenant, 'instance_id': iid, 'provider_message_id': data['message_id'], 'direction': 'outbound'}
        doc = await rt.db.messages.find_one(query)
        if not doc:
            return False
        if RANKS.get(doc['status'], 0) < RANKS[data['status']]:
            await rt.db.messages.update_one({**query, 'status': doc['status']},
                {'$set': {'status': data['status'], 'updated_at': now(), 'error_code': None}})
    else:
        phone_hash = rt.digest(f'{tenant}:{data["phone"]}')
        if data['message'].strip().upper() in ('SAIR', 'PARAR', 'STOP', 'CANCELAR'):
            await rt.db.suppressions.update_one({'tenant_id': tenant, 'phone_hash': phone_hash},
                {'$set': {'opted_out_at': now(), 'reason': rt.seal('Solicitação recebida por WhatsApp')}}, upsert=True)
            await rt.db.messages.update_many({'tenant_id': tenant, 'phone_hash': phone_hash,
                'status': {'$in': ['queued', 'retry_wait']}}, {'$set': {'status': 'cancelled', 'error_code': 'OPTED_OUT'}})
        try:
            await rt.db.messages.insert_one({'_id': new_id(), 'tenant_id': tenant, 'instance_id': iid,
                'provider_message_id': data['message_id'], 'direction': 'inbound', 'status': 'received',
                'phone_hash': phone_hash, 'content': rt.seal({'phone': data['phone'], 'message': data['message']}),
                'created_at': now(), 'content_expires_at': now() + timedelta(days=30), 'expires_at': now() + timedelta(days=90)})
        except DuplicateKeyError:
            pass
    return True
