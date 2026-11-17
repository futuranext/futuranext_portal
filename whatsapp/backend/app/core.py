"""Configuração, criptografia e infraestrutura compartilhadas pela API e worker."""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from typing import Literal
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pymongo import AsyncMongoClient
from redis.asyncio import Redis


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def fail(status: int, code: str, message: str):
    raise HTTPException(status, detail={'code': code, 'message': message})


class Settings(BaseSettings):
    mongo_uri: SecretStr
    redis_url: SecretStr
    evolution_url: str = 'http://evolution:8080'
    evolution_api_key: SecretStr
    data_encryption_key: SecretStr
    auth_pepper: SecretStr
    frontend_origins: str = 'http://localhost:3000,http://127.0.0.1:3000'
    cookie_secure: bool = False
    webhook_base_url: str = 'http://backend:8000'
    consent_base_url: str = 'http://localhost:3000'
    asaas_enabled: bool = False
    asaas_environment: Literal['sandbox', 'production'] = 'sandbox'
    asaas_api_key: SecretStr = SecretStr('')
    asaas_webhook_token: SecretStr = SecretStr('')
    zoho_api_key: SecretStr = SecretStr('')
    whatsapp_outbound_webhooks: SecretStr = SecretStr('')
    portal_service_token: SecretStr = SecretStr('')
    docs_enabled: bool = True
    email_from_address: str = 'suporte@sincronizar.com.br'
    email_from_name: str = 'UPFruit - Suporte'


class Runtime:
    def __init__(self, config: Settings):
        self.config = config
        self.cipher = Fernet(config.data_encryption_key.get_secret_value().encode())
        self.mongo = AsyncMongoClient(config.mongo_uri.get_secret_value(), tz_aware=True,
                                      serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
        self.db = self.mongo.get_default_database()
        self.redis = Redis.from_url(config.redis_url.get_secret_value(), decode_responses=True,
                                    socket_timeout=3, socket_connect_timeout=2)
        self.http = httpx.AsyncClient(base_url=config.evolution_url, timeout=20,
                                     headers={'apikey': config.evolution_api_key.get_secret_value(),
                                              'Origin': 'http://backend:8000'})
        from .providers import EvolutionProvider
        self.provider = EvolutionProvider(self.http)

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

    async def audit(self, tenant: str, actor: str, action: str, resource: str = ''):
        from datetime import timedelta
        await self.db.audit_events.insert_one({'_id': new_id(), 'tenant_id': tenant,
            'actor_id': actor, 'action': action, 'resource_id': resource, 'created_at': now(),
            'expires_at': now() + timedelta(days=180)})

    async def close(self):
        await self.http.aclose()
        await self.redis.aclose()
        await self.mongo.close()
