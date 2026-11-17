import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient
from backend.app import server as main


def test_liveness_does_not_claim_readiness(client):
    assert client.get('/health/live').json() == {'status': 'ok'}


def test_readiness_failure_is_sanitized(monkeypatch, client):
    monkeypatch.setattr(main, 'dependencies_ready', AsyncMock(return_value=False))
    with TestClient(main.app) as client:
        response = client.get('/health/ready')
        assert response.status_code == 503
        assert response.json() == {'status': 'unavailable'}
        assert response.headers['cache-control'] == 'no-store'


def test_readiness_success(monkeypatch, client):
    monkeypatch.setattr(main, 'dependencies_ready', AsyncMock(return_value=True))
    with TestClient(main.app) as client:
        assert client.get('/health/ready').json() == {'status': 'ready'}


def test_dependency_failure_is_not_leaked(client, rt):
    rt.mongo.admin.command.side_effect = RuntimeError('secret-uri')
    assert asyncio.run(main.dependencies_ready()) is False
