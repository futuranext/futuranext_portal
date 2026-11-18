"""Testes do serviço WhatsApp contra PostgreSQL real (o mesmo schema do portal).

Defina WA_TEST_MIGRATION_DATABASE_URL com o papel dono de um banco DESCARTÁVEL local, por exemplo
postgresql://postgres:SENHA@127.0.0.1:55432/futuranext_test. O fixture aplica as migrações Alembic
do portal (incluindo downgrade/upgrade da 0013), ativa os papéis wa_api e wa_worker com o comando
real `app.db_admin setup-whatsapp-roles` e semeia empresas, licenças e aceite.
Sem a variável, só os testes que não usam banco são executados.
"""
import asyncio
import base64
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

# Credenciais sintéticas, nunca usadas para contatar serviços.
os.environ['WA_DATABASE_URL'] = 'postgresql://wa_api:placeholder@127.0.0.1:1/unused'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['EVOLUTION_API_KEY'] = 'synthetic-test-only'
os.environ['DATA_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(b'0' * 32).decode()
os.environ['AUTH_PEPPER'] = 'synthetic-test-pepper-not-production'

if sys.platform == 'win32':
    # psycopg assíncrono não funciona no ProactorEventLoop (padrão do Windows). Produção roda em Linux.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PORTAL_BACKEND = Path(__file__).resolve().parents[2] / 'backend'
API_PASSWORD = 'wa-api-test-password-0123456789'
WORKER_PASSWORD = 'wa-worker-test-password-0123456789'
TABLES = ('webhook_deliveries', 'callback_destinations', 'gateway_events', 'audit_events', 'suppressions',
          'messages', 'api_key_instances', 'api_keys', 'instances')
INSTANCE_A = UUID('aaaaaaaa-0000-4000-8000-00000000000a')
INSTANCE_B = UUID('bbbbbbbb-0000-4000-8000-00000000000b')
WEBHOOK_SECRET = 'webhook-test'


class FakeRedis:
    def __init__(self):
        self.values, self.expiry = {}, {}

    async def get(self, key):
        if self.expiry.get(key, float('inf')) < time.time():
            self.values.pop(key, None)
        return self.values.get(key)

    async def set(self, key, value, ex=None, **kwargs):
        self.values[key] = value
        if ex:
            self.expiry[key] = time.time() + ex
        return True

    async def delete(self, key):
        self.values.pop(key, None)

    async def eval(self, script, count, key, seconds):
        value = int(await self.get(key) or 0) + 1
        await self.set(key, value)
        if value == 1:
            self.expiry[key] = time.time() + int(seconds)
        return value

    async def xadd(self, *args, **kwargs):
        return '1-0'

    async def ping(self):
        return True

    async def aclose(self):
        pass


def with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    netloc = f'{user}:{password}@{parts.hostname}:{parts.port or 5432}'
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ''))


@dataclass
class Postgres:
    owner_url: str
    api_url: str
    worker_url: str
    tenant_a: UUID
    tenant_b: UUID
    unlicensed: UUID
    actor: UUID
    migration: dict = field(default_factory=dict)

    def sql(self, query, params=(), *, fetch=True):
        with psycopg.connect(self.owner_url, autocommit=True, row_factory=dict_row) as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall() if fetch and cursor.description else None


def alembic(owner_url: str, *args: str) -> None:
    env = {**os.environ, 'FN_MIGRATION_DATABASE_URL': owner_url}
    subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=PORTAL_BACKEND, env=env, check=True,
                   capture_output=True, timeout=120)


def schema_state(conn) -> dict:
    return conn.execute('''
        SELECT to_regnamespace('whatsapp') IS NOT NULL AS schema,
               to_regclass('whatsapp.messages') IS NOT NULL AS messages,
               (SELECT version_num FROM futuranext_meta.alembic_version) AS revision''').fetchone()


@pytest.fixture(scope='session')
def pg() -> Postgres:
    from backend.app.core import postgres_dsn
    owner_url = os.environ.get('WA_TEST_MIGRATION_DATABASE_URL')
    if not owner_url:
        pytest.skip('Defina WA_TEST_MIGRATION_DATABASE_URL para o PostgreSQL descartável.')
    owner_url = postgres_dsn(owner_url)
    parts = urlsplit(owner_url)
    assert parts.hostname in {'localhost', '127.0.0.1', '::1', 'postgres'}, 'Somente banco local.'
    assert 'test' in parts.path, 'O nome do banco precisa conter "test".'
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as conn:
        # O Supabase real fornece auth.users e os papéis da Data API; aqui são simulados.
        conn.execute('CREATE SCHEMA IF NOT EXISTS auth')
        conn.execute('''CREATE TABLE IF NOT EXISTS auth.users (
            id uuid PRIMARY KEY, email text NOT NULL UNIQUE, email_confirmed_at timestamptz,
            encrypted_password text, invited_at timestamptz, last_sign_in_at timestamptz, raw_app_meta_data jsonb)''')
        conn.execute('''DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fn_app') THEN
                CREATE ROLE fn_app LOGIN PASSWORD 'fn-app-test-password-0123456789'; END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
        END $$''')

    alembic(owner_url, 'upgrade', 'head')
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as conn:
        migration = {'head': schema_state(conn)}
    alembic(owner_url, 'downgrade', '0012_whatsapp_consent')
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as conn:
        migration['downgraded'] = schema_state(conn)
        migration['portal_intact'] = conn.execute(
            "SELECT to_regclass('futuranext.whatsapp_consents') IS NOT NULL AND "
            "to_regclass('futuranext.whatsapp_events') IS NOT NULL AS ok").fetchone()['ok']
        # Pior caso: privilégios padrão globais para os papéis da Data API, como num projeto mal configurado.
        conn.execute('ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES TO anon, authenticated, service_role')
        conn.execute('ALTER DEFAULT PRIVILEGES GRANT ALL ON FUNCTIONS TO anon, authenticated, service_role')
    try:
        alembic(owner_url, 'upgrade', 'head')
    finally:
        with psycopg.connect(owner_url, autocommit=True) as conn:
            conn.execute('ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM anon, authenticated, service_role')
            conn.execute('ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM anon, authenticated, service_role')
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as conn:
        migration['upgraded'] = schema_state(conn)

    env = {**os.environ, 'FN_MIGRATION_DATABASE_URL': owner_url,
           'WA_API_DB_PASSWORD': API_PASSWORD, 'WA_WORKER_DB_PASSWORD': WORKER_PASSWORD}
    subprocess.run([sys.executable, '-m', 'app.db_admin', 'setup-whatsapp-roles'], cwd=PORTAL_BACKEND,
                   env=env, check=True, capture_output=True, timeout=60)

    suffix = uuid4().hex[:10]
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as conn:
        tenants = [conn.execute('INSERT INTO futuranext.organizations (slug, name) VALUES (%s, %s) RETURNING id',
                                (f'wa-{name}-{suffix}', f'Empresa {name}')).fetchone()['id']
                   for name in ('a', 'b', 'sem-licenca')]
        product = conn.execute("SELECT id FROM futuranext.products WHERE slug = 'whatsapp'").fetchone()['id']
        for tenant in tenants[:2]:
            conn.execute('INSERT INTO futuranext.tenant_products (tenant_id, product_id) VALUES (%s, %s)', (tenant, product))
        actor = uuid4()
        conn.execute('INSERT INTO auth.users (id, email, email_confirmed_at) VALUES (%s, %s, now())',
                     (actor, f'admin-{suffix}@example.test'))
        conn.execute('INSERT INTO futuranext.profiles (user_id, email) VALUES (%s, %s)', (actor, f'admin-{suffix}@example.test'))
    return Postgres(owner_url, with_credentials(owner_url, 'wa_api', API_PASSWORD),
                    with_credentials(owner_url, 'wa_worker', WORKER_PASSWORD), tenants[0], tenants[1], tenants[2],
                    actor, migration)


def build_runtime(url, redis, provider, http=None):
    from backend.app.core import Database, Runtime, Settings
    runtime = Runtime.__new__(Runtime)
    runtime.config = Settings()
    runtime.cipher = Fernet(runtime.config.data_encryption_key.get_secret_value().encode())
    runtime.db = Database(url, 4)
    runtime.redis = redis
    runtime.provider = provider
    if http is None:
        http = AsyncMock()
        http.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'http://gateway/'))
    runtime.http = http
    return runtime


@pytest.fixture
def rt(pg, monkeypatch):
    """Runtime da API (papel wa_api). Limpa as tabelas do serviço e semeia duas instâncias."""

    from backend.app import server
    pg.sql('TRUNCATE ' + ', '.join(f'whatsapp.{table}' for table in TABLES), fetch=False)
    pg.sql('DELETE FROM futuranext.whatsapp_consents WHERE tenant_id = ANY(%s)',
           ([pg.tenant_a, pg.tenant_b, pg.unlicensed],), fetch=False)
    provider = SimpleNamespace(create=AsyncMock(return_value={}), connect=AsyncMock(return_value={}),
        connection=AsyncMock(return_value='connected'), delete=AsyncMock(), disconnect=AsyncMock(),
        send_text=AsyncMock(return_value={'key': {'id': 'provider-message-1'}}))
    runtime = build_runtime(pg.api_url, FakeRedis(), provider)
    for instance, tenant in ((INSTANCE_A, pg.tenant_a), (INSTANCE_B, pg.tenant_b)):
        pg.sql('''INSERT INTO whatsapp.instances (id, tenant_id, name, provider_ref, connection_state, webhook_hash,
                      webhook_secret) VALUES (%s, %s, %s, %s, 'connected', %s, %s)''',
               (instance, tenant, f'instancia-{instance.hex[:4]}', f'wp_{instance}',
                runtime.digest(WEBHOOK_SECRET), runtime.seal(WEBHOOK_SECRET)), fetch=False)
    monkeypatch.setattr(server, 'Runtime', lambda config: runtime)
    runtime.pg = pg
    return runtime


@pytest.fixture
def client(rt):
    from backend.app.server import app
    with TestClient(app) as value:
        yield value


def issue_key(rt, tenant, instances, scopes=('instances:read', 'messages:read', 'messages:send'), **columns):
    """Cria uma chave como o portal faria e devolve o segredo em texto (somente no teste)."""
    import secrets
    secret = 'wp_' + secrets.token_urlsafe(32)
    key_id = uuid4()
    rt.pg.sql('''INSERT INTO whatsapp.api_keys (id, tenant_id, name, prefix, secret_hash, scopes, created_at, expires_at,
                     revoked_at) VALUES (%s, %s, 'Teste', %s, %s, %s, %s, %s, %s)''',
              (key_id, tenant, secret[:10], rt.digest(secret), list(scopes), time_from_now(days=-2),
               columns.get('expires_at') or time_from_now(days=30), columns.get('revoked_at')), fetch=False)
    for instance in instances:
        rt.pg.sql('INSERT INTO whatsapp.api_key_instances (tenant_id, key_id, instance_id) VALUES (%s, %s, %s)',
                  (tenant, key_id, instance), fetch=False)
    return secret


def time_from_now(**delta):
    from datetime import timedelta

    from backend.app.core import now
    return now() + timedelta(**delta)


@pytest.fixture
def bot(client, rt):
    """Cliente autenticado com a chave da empresa A (escopos emitidos pelo portal)."""
    client.headers['Authorization'] = 'Bearer ' + issue_key(rt, rt.pg.tenant_a, [INSTANCE_A])
    return client


@pytest.fixture
def work(pg, rt):
    """Executa uma rotina do worker com um runtime próprio (papel wa_worker) e o mesmo Redis/gateway."""
    def run(func, *args, **kwargs):
        async def scenario():
            runtime = build_runtime(pg.worker_url, rt.redis, rt.provider)
            await runtime.db.open()
            try:
                return await func(runtime, *args, **kwargs)
            finally:
                await runtime.db.close()
        return asyncio.run(scenario())
    return run
