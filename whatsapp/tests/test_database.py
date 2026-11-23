"""Migração 0013, privilégios, RLS, exposição na Data API, retenção e concorrência do worker."""
import asyncio
import tomllib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from backend.app.core import now
from backend.app.worker import apply_retention, dispatch_one
from conftest import INSTANCE_A, INSTANCE_B
from psycopg.rows import dict_row

TABLES = ('instances', 'api_keys', 'api_key_instances', 'messages', 'suppressions', 'gateway_events',
          'callback_destinations', 'webhook_deliveries', 'audit_events')


def connect(url):
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def test_migration_is_reversible_and_keeps_portal_tables(pg):
    assert pg.migration['head'] == {'schema': True, 'messages': True, 'revision': '0014_commercial'}
    assert pg.migration['downgraded'] == {'schema': False, 'messages': False, 'revision': '0012_whatsapp_consent'}
    assert pg.migration['portal_intact'] is True
    assert pg.migration['upgraded'] == pg.migration['head']


def test_runtime_roles_have_no_special_attributes(pg):
    rows = pg.sql('''SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
                            rolconnlimit FROM pg_roles WHERE rolname IN ('wa_api', 'wa_worker') ORDER BY 1''')
    assert [row['rolname'] for row in rows] == ['wa_api', 'wa_worker']
    for row in rows:
        assert row['rolcanlogin'] and row['rolconnlimit'] == 20
        assert not any(row[key] for key in ('rolsuper', 'rolcreaterole', 'rolcreatedb', 'rolreplication', 'rolbypassrls'))


def test_supabase_api_roles_have_no_access_even_with_hostile_defaults(pg):
    rows = pg.sql('''
        SELECT role, has_schema_privilege(role, 'whatsapp', 'USAGE') AS usage,
               bool_or(has_table_privilege(role, format('whatsapp.%%I', c.relname),
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) AS tables,
               bool_or(has_function_privilege(role, p.oid, 'EXECUTE')) AS functions
        FROM unnest(ARRAY['anon', 'authenticated', 'service_role', 'fn_app']) AS role
        CROSS JOIN pg_class c CROSS JOIN pg_proc p
        WHERE c.relnamespace = 'whatsapp'::regnamespace AND c.relkind = 'r'
          AND p.pronamespace = 'whatsapp'::regnamespace
        GROUP BY role''')
    assert rows and all(not (row['usage'] or row['tables'] or row['functions']) for row in rows), rows


def test_private_schema_is_not_exposed_by_data_api():
    config = tomllib.loads((Path(__file__).resolve().parents[2] / 'supabase' / 'config.toml').read_text(encoding='utf-8'))
    assert 'whatsapp' not in config['api']['schemas'] and 'evolution' not in config['api']['schemas']


def test_every_table_forces_rls(pg):
    rows = pg.sql('''SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
                     WHERE relnamespace = 'whatsapp'::regnamespace AND relkind = 'r' ''')
    assert sorted(row['relname'] for row in rows) == sorted(TABLES)
    assert all(row['relrowsecurity'] and row['relforcerowsecurity'] for row in rows)


def test_api_role_only_sees_the_transaction_tenant(pg, rt):
    with connect(pg.api_url) as conn:
        assert conn.execute('SELECT count(*) AS total FROM whatsapp.instances').fetchone()['total'] == 0
        with conn.transaction():
            conn.execute("SELECT set_config('whatsapp.tenant_id', %s, true)", (str(pg.tenant_a),))
            ids = [row['id'] for row in conn.execute('SELECT id FROM whatsapp.instances')]
            assert ids == [INSTANCE_A]
            assert conn.execute("UPDATE whatsapp.instances SET name = 'x' WHERE id = %s", (INSTANCE_B,)).rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT set_config('whatsapp.tenant_id', %s, true)", (str(pg.tenant_a),))
                conn.execute('''INSERT INTO whatsapp.suppressions (tenant_id, phone_hash) VALUES (%s, %s)''',
                             (pg.tenant_b, 'f' * 64))
        for statement in ('SELECT * FROM futuranext.organizations', 'SELECT * FROM futuranext.whatsapp_consents',
                          'SELECT * FROM whatsapp.webhook_deliveries', 'DELETE FROM whatsapp.messages',
                          'UPDATE whatsapp.messages SET content = NULL'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_worker_role_cannot_create_keys_or_read_portal(pg, rt):
    with connect(pg.worker_url) as conn:
        assert conn.execute('SELECT count(*) AS total FROM whatsapp.instances').fetchone()['total'] == 2
        for statement in ('SELECT * FROM whatsapp.api_keys', 'SELECT * FROM futuranext.tenant_products',
                          "SELECT * FROM whatsapp.api_key_identity('x')",
                          'DELETE FROM whatsapp.instances'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_cross_tenant_key_link_is_rejected_by_foreign_key(pg, rt):
    key = uuid4()
    pg.sql('''INSERT INTO whatsapp.api_keys (id, tenant_id, name, prefix, secret_hash, scopes, expires_at)
              VALUES (%s, %s, 'k', 'wp_0123456', %s, ARRAY['messages:read'], now() + interval '1 day')''',
           (key, pg.tenant_a, 'e' * 64), fetch=False)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        pg.sql('INSERT INTO whatsapp.api_key_instances (tenant_id, key_id, instance_id) VALUES (%s, %s, %s)',
               (pg.tenant_a, key, INSTANCE_B), fetch=False)
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.sql('''INSERT INTO whatsapp.api_keys (id, tenant_id, name, prefix, secret_hash, scopes, expires_at)
                  VALUES (%s, %s, 'k', 'wp_0123456', %s, ARRAY['keys:write'], now() + interval '1 day')''',
               (uuid4(), pg.tenant_a, 'd' * 64), fetch=False)


def test_deleting_instance_cascades_operational_rows_but_keeps_history(bot, rt, pg):
    sent = bot.post('/api/v1/messages/send', json={'instance_id': str(INSTANCE_A), 'phone': '5511999999999',
                                                   'message': 'oi', 'idempotency_key': 'k'}).json()
    pg.sql('''INSERT INTO whatsapp.gateway_events (event_key, tenant_id, instance_id, type, data, expires_at)
              VALUES (%s, %s, %s, 'connection.update', 'x', now() + interval '1 day')''',
           ('c' * 64, pg.tenant_a, INSTANCE_A), fetch=False)
    pg.sql('DELETE FROM whatsapp.instances WHERE id = %s', (INSTANCE_A,), fetch=False)
    assert pg.sql('SELECT count(*) AS total FROM whatsapp.gateway_events')[0]['total'] == 0
    assert pg.sql('SELECT count(*) AS total FROM whatsapp.api_key_instances')[0]['total'] == 0
    assert pg.sql('SELECT id FROM whatsapp.messages') == [{'id': UUID(sent['id'])}]


def test_retention_matches_previous_ttls(rt, pg, work):
    past, future = now() - timedelta(seconds=1), now() + timedelta(days=1)
    for index, (content_expiry, expiry) in enumerate(((past, future), (past, past), (future, future))):
        pg.sql('''INSERT INTO whatsapp.messages (id, tenant_id, instance_id, direction, status, provider_message_id,
                      phone_hash, content, content_expires_at, expires_at)
                  VALUES (%s, %s, %s, 'inbound', 'received', %s, %s, 'cifrado', %s, %s)''',
               (uuid4(), pg.tenant_a, INSTANCE_A, f'm{index}', 'b' * 64, content_expiry, expiry), fetch=False)
    pg.sql('''INSERT INTO whatsapp.audit_events (tenant_id, actor_id, action, expires_at)
              VALUES (%s, 'teste', 'antigo', %s), (%s, 'teste', 'atual', %s)''',
           (pg.tenant_a, past, pg.tenant_a, future), fetch=False)
    pg.sql('''INSERT INTO whatsapp.gateway_events (event_key, tenant_id, instance_id, type, data, expires_at)
              VALUES (%s, %s, %s, 'connection.update', 'x', %s)''', ('9' * 64, pg.tenant_a, INSTANCE_A, past), fetch=False)
    work(apply_retention)
    rows = pg.sql('SELECT provider_message_id, content FROM whatsapp.messages ORDER BY provider_message_id')
    assert rows == [{'provider_message_id': 'm0', 'content': None}, {'provider_message_id': 'm2', 'content': 'cifrado'}]
    assert [row['action'] for row in pg.sql('SELECT action FROM whatsapp.audit_events')] == ['atual']
    assert pg.sql('SELECT count(*) AS total FROM whatsapp.gateway_events')[0]['total'] == 0


def test_concurrent_workers_send_each_message_exactly_once(bot, rt, pg, work):
    for index in range(10):
        response = bot.post('/api/v1/messages/send', json={'instance_id': str(INSTANCE_A), 'phone': '5511999999999',
                                                          'message': f'm{index}', 'idempotency_key': f'k{index}'})
        assert response.status_code == 202
    async def unlimited(*args, **kwargs):
        return None

    async def provider_send(reference, phone, message):
        await asyncio.sleep(0.01)
        return {'key': {'id': f'provider-{message}'}}
    rt.provider.send_text.side_effect = provider_send

    async def scenario(runtime):
        # O limite por instância (1 envio a cada 5 s) fica fora deste teste, que mede só a fila.
        runtime.limit = unlimited
        async def worker():
            for _ in range(10):
                await dispatch_one(runtime)
        await asyncio.gather(*(worker() for _ in range(4)))

    work(scenario)
    sent = [call.args[2] for call in rt.provider.send_text.await_args_list]
    assert sorted(sent) == sorted(f'm{index}' for index in range(10))
    rows = pg.sql('SELECT status, attempts, provider_message_id FROM whatsapp.messages')
    assert {row['status'] for row in rows} == {'sent'} and {row['attempts'] for row in rows} == {1}
    assert sorted(row['provider_message_id'] for row in rows) == sorted(f'provider-m{index}' for index in range(10))
