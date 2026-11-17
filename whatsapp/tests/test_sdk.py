import httpx
import pytest
from integrations.python.whatsapp_client import WhatsAppClient


def test_sdk_preserves_queue_state_and_idempotency():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(202, json={'id': 'msg-1', 'status': 'queued'})
    client = WhatsAppClient('http://local.test', 'synthetic', 'instance-1')
    client.http.close()
    client.http = httpx.Client(base_url='http://local.test', transport=httpx.MockTransport(handler))
    with client:
        assert client.send_text('5511999999999', 'Teste', 'order-123')['status'] == 'queued'
    assert len(seen) == 1 and b'order-123' in seen[0].content


def test_sdk_does_not_retry_ambiguous_failure():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(503)
    client = WhatsAppClient('http://local.test', 'synthetic', 'instance-1')
    client.http.close()
    client.http = httpx.Client(base_url='http://local.test', transport=httpx.MockTransport(handler))
    with client, pytest.raises(httpx.HTTPStatusError):
        client.send_text('5511999999999', 'Teste', 'stable-key')
    assert len(seen) == 1
