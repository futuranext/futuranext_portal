import os
import base64
import time
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

# Credenciais sintéticas, nunca usadas para contatar serviços.
os.environ['MONGO_URI'] = 'mongodb://localhost:27017/test'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['EVOLUTION_API_KEY'] = 'synthetic-test-only'
os.environ['DATA_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(b'0' * 32).decode()
os.environ['AUTH_PEPPER'] = 'synthetic-test-pepper-not-production'


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


@pytest.fixture
def rt(monkeypatch):
    from backend.app.core import Runtime, Settings, now
    from backend.app import server
    from backend.app.auth import passwords
    runtime = Runtime.__new__(Runtime)
    runtime.config = Settings()
    runtime.cipher = Fernet(runtime.config.data_encryption_key.get_secret_value().encode())
    runtime.mongo = SimpleNamespace(admin=SimpleNamespace(command=AsyncMock(return_value={'ok': 1})), close=AsyncMock())
    runtime.db = AsyncMongoMockClient(tz_aware=True).platform
    runtime.redis = FakeRedis()
    runtime.http = AsyncMock()
    runtime.http.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'http://gateway/'))
    runtime.provider = SimpleNamespace(create=AsyncMock(return_value={}), connect=AsyncMock(return_value={}),
        connection=AsyncMock(return_value='connected'), delete=AsyncMock(), disconnect=AsyncMock(),
        send_text=AsyncMock(return_value={'key': {'id': 'provider-message-1'}}))
    monkeypatch.setattr(server, 'Runtime', lambda config: runtime)
    async def seed():
        await runtime.db.tenants.insert_one({'_id': 'tenant-a', 'name': 'Empresa de teste'})
        await runtime.db.users.insert_one({'_id': 'user-a', 'tenant_id': 'tenant-a', 'email': 'admin@example.test',
            'password_hash': passwords.hash('Test-password-123!'), 'role': 'tenant_admin', 'disabled': False})
        for iid, tenant in [('instance-a', 'tenant-a'), ('instance-b', 'tenant-b')]:
            await runtime.db.instances.insert_one({'_id': iid, 'tenant_id': tenant, 'name': iid,
                'provider_ref': 'wp_' + iid, 'connection_state': 'connected', 'created_at': now(),
                'webhook_hash': runtime.digest('webhook-test'), 'webhook_secret': runtime.seal('webhook-test')})
    asyncio.run(seed())
    return runtime


@pytest.fixture
def client(rt):
    from backend.app.server import app
    with TestClient(app, headers={'Origin': 'http://localhost:3000'}) as value:
        yield value


@pytest.fixture
def admin(client):
    response = client.post('/api/v1/auth/login', json={'email': 'admin@example.test', 'password': 'Test-password-123!'})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return client
