"""Normalização e aplicação idempotente de eventos do gateway."""
import re
from datetime import timedelta

from .core import fetch_one, new_id, now
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


async def process_event(rt, conn, event):
    """Aplica um evento na transação do worker. Retorna False quando ainda não há correlação."""
    from .api import suppress
    data = rt.unseal(event['data'])
    tenant, iid = event['tenant_id'], event['instance_id']
    instance = await fetch_one(conn, 'SELECT * FROM whatsapp.instances WHERE id = %s AND tenant_id = %s', (iid, tenant))
    if not instance:
        return True
    if event['type'] == 'connection.update':
        state = data['state']
        update = {'connection_state': state, 'observed_at': now()}
        if state in ('connected', 'disconnected') and state != instance['connection_state']:
            update['last_connected_at' if state == 'connected' else 'last_disconnected_at'] = now()
            await rt.audit(conn, tenant, 'gateway', 'instance.connection.' + state, iid)
        phone = data.get('wuid', '').split('@')[0]
        if phone:
            update['phone_masked'] = '•••• ' + phone[-4:]
            update['connected_phone'] = rt.seal(phone)
        update['last_event_at'] = event['created_at']
        assignments = ', '.join(f'{column} = %s' for column in update)
        await conn.execute(
            f'UPDATE whatsapp.instances SET {assignments} WHERE id = %s AND tenant_id = %s '
            "AND connection_state <> 'deleting' AND (last_event_at IS NULL OR last_event_at < %s)",
            (*update.values(), iid, tenant, event['created_at']))
        if state == 'connected':
            await rt.redis.delete(f'qr:{iid}')
    elif event['type'] == 'messages.update':
        doc = await fetch_one(conn, '''
            SELECT id, status FROM whatsapp.messages WHERE tenant_id = %s AND instance_id = %s
              AND provider_message_id = %s AND direction = 'outbound' ORDER BY created_at LIMIT 1''',
            (tenant, iid, data['message_id']))
        if not doc:
            return False
        if RANKS.get(doc['status'], 0) < RANKS[data['status']]:
            await conn.execute('UPDATE whatsapp.messages SET status = %s, updated_at = now(), error_code = NULL '
                               'WHERE id = %s AND tenant_id = %s AND status = %s',
                               (data['status'], doc['id'], tenant, doc['status']))
    else:
        phone_hash = rt.digest(f'{tenant}:{data["phone"]}')
        if data['message'].strip().upper() in ('SAIR', 'PARAR', 'STOP', 'CANCELAR'):
            await suppress(rt, conn, tenant, phone_hash, 'Solicitação recebida por WhatsApp')
        created = now()
        await conn.execute('''
            INSERT INTO whatsapp.messages (id, tenant_id, instance_id, provider_message_id, direction, status,
                phone_hash, content, created_at, content_expires_at, expires_at)
            VALUES (%s, %s, %s, %s, 'inbound', 'received', %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, instance_id, provider_message_id) WHERE direction = 'inbound' DO NOTHING''',
            (new_id(), tenant, iid, data['message_id'], phone_hash,
             rt.seal({'phone': data['phone'], 'message': data['message']}), created,
             created + timedelta(days=30), created + timedelta(days=90)))
    return True
