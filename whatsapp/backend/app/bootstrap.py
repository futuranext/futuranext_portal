"""Bootstrap local interativo: docker compose exec backend python -m app.bootstrap."""
import asyncio
import getpass
import re
from .core import Runtime, Settings, now
from .auth import passwords
from .migrations import migrate


async def main():
    rt = Runtime(Settings())
    try:
        await migrate(rt)
        if await rt.db.users.count_documents({}):
            print('Já existe usuário cadastrado. Bootstrap não altera credenciais existentes.')
            return
        company = input('Nome da empresa: ').strip()
        if (not company or len(company) > 100 or any(ord(c) < 32 for c in company)
                or any(token in company.lower() for token in ('^c', 'executionpolicy', 'activate.ps1'))):
            raise ValueError('Digite somente o nome da empresa, sem comandos do terminal.')
        email = input('E-mail do administrador: ').strip().lower()
        if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('Informe um e-mail válido.')
        password = getpass.getpass('Senha (mínimo 12 caracteres): ')
        confirmation = getpass.getpass('Confirme a senha: ')
        if not 12 <= len(password) <= 256 or password != confirmation:
            raise ValueError('Senha inválida ou confirmação diferente.')
        # Entrega cancelamento pendente do terminal antes de qualquer gravação.
        await asyncio.sleep(0)
        await rt.db.tenants.update_one({'_id': 'local-company'}, {'$setOnInsert': {'name': company, 'created_at': now()}}, upsert=True)
        await rt.db.users.insert_one({'_id': 'local-admin', 'tenant_id': 'local-company', 'email': email,
            'password_hash': passwords.hash(password), 'role': 'global_admin', 'disabled': False, 'created_at': now()})
        await rt.audit('local-company', 'local-admin', 'admin.bootstrap')
        print('Administrador criado. Acesse http://localhost:3000 para entrar.')
    finally:
        await rt.close()


def cli():
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print('\nOperação interrompida. Execute o comando novamente para verificar ou criar o acesso.')
        return 130
    except ValueError as exc:
        print(f'Cadastro não concluído: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
