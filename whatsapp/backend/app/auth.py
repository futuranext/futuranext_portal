"""Autenticação da API pública: chave `wp_...` com escopos, instâncias e empresa obrigatórias.

O login de usuários, as contas e a cobrança pertencem ao portal FuturaNext. Este serviço só aceita
chaves emitidas pelo portal e o token de serviço interno (veja portal_service.py).
"""
from dataclasses import dataclass

from fastapi import Request

from .core import fail, fetch_one


@dataclass
class Identity:
    tenant_id: str
    actor_id: str
    scopes: list[str]
    instance_ids: list[str] | None
    session: bool = False


async def identity(request: Request, scope: str = '') -> Identity:
    rt = request.app.state.rt
    auth = request.headers.get('authorization', '')
    if not auth.startswith('Bearer '):
        fail(401, 'UNAUTHORIZED', 'Informe sua API Key.')
    # A função do banco só devolve chaves ativas, não expiradas e de empresa com licença vigente.
    async with rt.db.tx() as conn:
        row = await fetch_one(conn, 'SELECT * FROM whatsapp.api_key_identity(%s)', (rt.digest(auth[7:]),))
    if not row:
        fail(401, 'UNAUTHORIZED', 'Chave inválida ou expirada.')
    user = Identity(str(row['tenant_id']), str(row['id']), list(row['scopes']),
                    [str(item) for item in row['instance_ids']])
    if scope and scope not in user.scopes:
        fail(403, 'FORBIDDEN', 'Permissão insuficiente para esta ação.')
    if request.headers.get('x-tenant-id'):
        fail(403, 'FORBIDDEN', 'Somente o administrador global pode selecionar outra conta.')
    await rt.limit(f'actor:{user.actor_id}', 120)
    return user


def check_instance(user: Identity, instance_id: str) -> None:
    if user.instance_ids is not None and instance_id not in user.instance_ids:
        fail(404, 'NOT_FOUND', 'Instância não encontrada.')
