from uuid import UUID

from fastapi import HTTPException

from ..repository import Repository


def require_tool(repo: Repository, tenant_id: UUID, tool_slug: str, *, write: bool = False) -> None:
    if not repo.can_use_tool(tenant_id, tool_slug):
        raise HTTPException(404, "Ferramenta indisponível para esta empresa.")
    if write and not repo.can_use_tool(tenant_id, tool_slug, write=True):
        raise HTTPException(403, "Somente administradores do cliente podem alterar esta ferramenta.")
