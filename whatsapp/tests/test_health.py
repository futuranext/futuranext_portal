import asyncio
from unittest.mock import AsyncMock

import httpx

from backend.app import server as main


def test_liveness_does_not_claim_readiness(client):
    assert client.get('/health/live').json() == {'status': 'ok'}


def test_readiness_checks_postgres_redis_and_gateway(client, rt):
    response = client.get('/health/ready')
    assert response.status_code == 200 and response.json() == {'status': 'ready'}
    assert response.headers['cache-control'] == 'no-store'
    rt.http.get.return_value = httpx.Response(500, request=httpx.Request('GET', 'http://gateway/'))
    failed = client.get('/health/ready')
    assert failed.status_code == 503 and failed.json() == {'status': 'unavailable'}
    rt.http.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'http://gateway/'))
    rt.redis.ping = AsyncMock(side_effect=RuntimeError('secret-redis-uri'))
    assert client.get('/health/ready').status_code == 503


def test_readiness_fails_when_database_is_unreachable(pg, rt):
    from backend.app.core import Database
    rt.db = Database('postgresql://wa_api:secret-password@127.0.0.1:1/futuranext_test', 1)

    async def scenario():
        await rt.db.open()
        try:
            main.app.state.rt = rt
            return await main.dependencies_ready()
        finally:
            await rt.db.close()
    assert asyncio.run(scenario()) is False


def test_readiness_failure_is_sanitized(client, monkeypatch):
    monkeypatch.setattr(main, 'dependencies_ready', AsyncMock(return_value=False))
    response = client.get('/health/ready')
    assert response.status_code == 503 and response.json() == {'status': 'unavailable'}


def test_asgi_entrypoint_exports_the_app():
    # O contêiner roda `uvicorn app.main:app`; um lint automático já removeu esse import uma vez.
    from backend.app import main as entrypoint
    assert entrypoint.app is main.app
