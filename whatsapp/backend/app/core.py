"""Configuração, criptografia e infraestrutura compartilhadas pela API e worker."""
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings
from redis.asyncio import Redis


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def fail(status: int, code: str, message: str):
    raise HTTPException(status, detail={'code': code, 'message': message})


def as_uuid(value) -> UUID | None:
    """Converte identificadores externos; valores inválidos nunca chegam ao SQL."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def postgres_dsn(value: str) -> str:
    """Aceita a mesma URL do portal (postgresql+psycopg://) e devolve a forma libpq."""
    for prefix in ('postgresql+psycopg://', 'postgres://'):
        if value.startswith(prefix):
            return 'postgresql://' + value[len(prefix):]
    if not value.startswith('postgresql://'):
        raise ValueError('WA_DATABASE_URL precisa ser uma URL PostgreSQL.')
    return value


class Settings(BaseSettings):
    database_url: SecretStr = Field(validation_alias=AliasChoices('WA_DATABASE_URL'))
    database_pool_size: int = Field(default=5, ge=1, le=20, validation_alias=AliasChoices('WA_DATABASE_POOL_SIZE'))
    redis_url: SecretStr
    evolution_url: str = 'http://evolution:8080'
    evolution_api_key: SecretStr
    data_encryption_key: SecretStr
    auth_pepper: SecretStr
    webhook_base_url: str = 'http://backend:8000'
    whatsapp_outbound_webhooks: SecretStr = SecretStr('')
    portal_service_token: SecretStr = SecretStr('')
    docs_enabled: bool = True


class Database:
    """Pool assíncrono. Toda consulta roda numa transação curta com tenant explícito ou nenhum."""

    def __init__(self, url: str, max_size: int = 5):
        self.pool = AsyncConnectionPool(
            postgres_dsn(url), min_size=1, max_size=max_size, open=False, timeout=5,
            kwargs={'autocommit': True, 'row_factory': dict_row, 'prepare_threshold': None,
                    'connect_timeout': 3, 'application_name': 'futuranext-whatsapp'},
            check=AsyncConnectionPool.check_connection)

    async def open(self):
        await self.pool.open(wait=False)

    async def close(self):
        await self.pool.close()

    @asynccontextmanager
    async def tx(self, tenant: UUID | str | None = None):
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('whatsapp.tenant_id', %s, true), "
                    "set_config('statement_timeout', '5000', true)",
                    (str(tenant) if tenant else '',))
                yield conn

    async def ready(self) -> bool:
        async with self.tx() as conn:
            row = await (await conn.execute(
                "SELECT to_regclass('whatsapp.messages') IS NOT NULL AS ok")).fetchone()
            return bool(row and row['ok'])


async def fetch_one(conn: AsyncConnection, query, params=()):
    return await (await conn.execute(query, params)).fetchone()


async def fetch_all(conn: AsyncConnection, query, params=()):
    return await (await conn.execute(query, params)).fetchall()


class Runtime:
    def __init__(self, config: Settings):
        self.config = config
        self.cipher = Fernet(config.data_encryption_key.get_secret_value().encode())
        self.db = Database(config.database_url.get_secret_value(), config.database_pool_size)
        self.redis = Redis.from_url(config.redis_url.get_secret_value(), decode_responses=True,
                                    socket_timeout=3, socket_connect_timeout=2)
        self.http = httpx.AsyncClient(base_url=config.evolution_url, timeout=20,
                                     headers={'apikey': config.evolution_api_key.get_secret_value(),
                                              'Origin': 'http://backend:8000'})
        from .providers import EvolutionProvider
        self.provider = EvolutionProvider(self.http)

    async def open(self):
        await self.db.open()

    def digest(self, value: str) -> str:
        return hmac.new(self.config.auth_pepper.get_secret_value().encode(),
                        value.encode(), hashlib.sha256).hexdigest()

    def seal(self, value) -> str:
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def unseal(self, value: str):
        return json.loads(self.cipher.decrypt(value.encode()))

    async def limit(self, key: str, maximum: int, seconds: int = 60):
        try:
            count = await self.redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                1, f'limit:{key}', seconds)
        except Exception:
            fail(503, 'LIMITER_UNAVAILABLE', 'Controle de limites indisponível. Tente novamente.')
        if int(count) > maximum:
            raise HTTPException(429, detail={'code': 'RATE_LIMIT', 'message': 'Limite atingido. Aguarde para tentar novamente.'},
                                headers={'Retry-After': str(seconds)})

    async def audit(self, conn: AsyncConnection, tenant, actor: str, action: str, resource='') -> None:
        """Registra na mesma transação da alteração auditada (retenção de 180 dias)."""
        await conn.execute(
            'INSERT INTO whatsapp.audit_events (tenant_id, actor_id, action, resource_id, expires_at) '
            'VALUES (%s, %s, %s, %s, %s)',
            (tenant, str(actor)[:100], action, str(resource or '')[:100], now() + timedelta(days=180)))

    async def close(self):
        await self.http.aclose()
        await self.redis.aclose()
        await self.db.close()
