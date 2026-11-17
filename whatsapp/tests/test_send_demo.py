from unittest.mock import MagicMock

import httpx
import pytest

from examples import testar_envio as demo


@pytest.fixture
def setup_demo(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_status.return_value = {'connection_state': 'connected'}
    client.send_text.return_value = {'id': 'msg-test', 'status': 'queued'}
    client.get_message_status.return_value = {'status': 'delivered'}
    monkeypatch.setattr(demo, 'WhatsAppClient', lambda *args: client)
    monkeypatch.setattr(demo, 'select_instance', lambda key: 'instance-test')
    monkeypatch.setattr(demo.getpass, 'getpass', lambda prompt: 'synthetic-test-key')
    return client


def test_demo_sends_once_with_explicit_key(setup_demo, monkeypatch):
    answers = iter(['5511999999999', 'Teste sintético', 'pedido-estavel', 'ENVIAR'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert demo.run() == 0
    setup_demo.send_text.assert_called_once_with('5511999999999', 'Teste sintético', idempotency_key='pedido-estavel')


def test_demo_cancel_does_not_send(setup_demo, monkeypatch):
    answers = iter(['5511999999999', 'Teste', '', 'nao'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert demo.run() == 0
    setup_demo.send_text.assert_not_called()


def test_demo_query_only_never_sends(setup_demo):
    assert demo.run('msg-test') == 0
    setup_demo.send_text.assert_not_called()
    setup_demo.get_status.assert_not_called()


def test_demo_does_not_retry_timeout(setup_demo, monkeypatch):
    answers = iter(['5511999999999', 'Teste', 'pedido-estavel', 'ENVIAR'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    setup_demo.send_text.side_effect = httpx.ReadTimeout('timeout')
    with pytest.raises(httpx.ReadTimeout):
        demo.run()
    assert setup_demo.send_text.call_count == 1
