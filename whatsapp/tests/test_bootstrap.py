import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.app import bootstrap


def test_rejects_terminal_command_before_password_or_writes(rt, monkeypatch):
    asyncio.run(rt.db.users.delete_many({}))
    monkeypatch.setattr(bootstrap, 'Runtime', lambda config: rt)
    monkeypatch.setattr(bootstrap, 'migrate', AsyncMock())
    monkeypatch.setattr('builtins.input', lambda prompt: '^C(Set-ExecutionPolicy -Scope Process)')
    monkeypatch.setattr(bootstrap.getpass, 'getpass', lambda prompt: pytest.fail('Não pedir senha'))
    with pytest.raises(ValueError, match='somente o nome'):
        asyncio.run(bootstrap.main())
    assert asyncio.run(rt.db.users.count_documents({})) == 0
    assert asyncio.run(rt.db.tenants.count_documents({'_id': 'local-company'})) == 0


def test_interrupt_has_friendly_output(monkeypatch, capsys):
    async def interrupted():
        raise KeyboardInterrupt()
    monkeypatch.setattr(bootstrap, 'main', interrupted)
    assert bootstrap.cli() == 130
    output = capsys.readouterr()
    assert 'Operação interrompida' in output.out
    assert 'Traceback' not in output.err
