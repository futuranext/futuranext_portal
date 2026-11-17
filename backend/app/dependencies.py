from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import AuthUnavailable, Identity, InvalidSession, SupabaseAuth
from .database import Database
from .repository import Repository

bearer_auth = HTTPBearer(auto_error=False)
Token = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_auth)]


def get_auth(request: Request) -> SupabaseAuth:
    auth = request.app.state.auth
    if auth is None:
        raise HTTPException(503, "Autenticação indisponível.")
    return auth


Auth = Annotated[SupabaseAuth, Depends(get_auth)]


def authenticate(auth: Auth, token: Token) -> Identity:
    if not token:
        raise HTTPException(401, "Entre na sua conta.", headers={"WWW-Authenticate": "Bearer"})
    try:
        return auth.identity(token.credentials)
    except InvalidSession as exc:
        raise HTTPException(
            401, "Sessão inválida ou expirada.", headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AuthUnavailable as exc:
        raise HTTPException(503, "Autenticação indisponível.") from exc


AuthenticatedSession = Annotated[Identity, Depends(authenticate)]


def require_verified_session(identity: AuthenticatedSession) -> Identity:
    if identity.first_password_required:
        raise HTTPException(403, "Troque a senha temporária antes de acessar o portal.")
    if identity.aal != "aal2":
        raise HTTPException(403, "Conclua a verificação em duas etapas.")
    return identity


CurrentUser = Annotated[Identity, Depends(require_verified_session)]


def get_database(request: Request) -> Database:
    database = request.app.state.database
    if database is None:
        raise HTTPException(503, "Banco de dados indisponível.")
    return database


Data = Annotated[Database, Depends(get_database)]


@contextmanager
def tenant_repository(
    database: Database,
    identity: Identity,
    tenant_id: UUID | None = None,
) -> Iterator[Repository]:
    with database.session(identity.user_id, identity.aal, tenant_id) as connection:
        repository = Repository(connection)
        if not repository.password_session_current(identity.password_version):
            raise HTTPException(401, "A sessão foi substituída. Entre novamente.")
        yield repository


def require_permission(repository: Repository, permission: str, status: int = 403) -> None:
    if not repository.has_permission(permission):
        detail = "Recurso indisponível." if status == 404 else "Ação não autorizada."
        raise HTTPException(status, detail)
