from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from . import schemas as s
from .auth import AuthUnavailable, InvalidSession, PasswordActionRejected
from .dependencies import (
    Auth,
    AuthenticatedSession,
    CurrentUser,
    Data,
    require_permission,
    tenant_repository,
)
from .repository import Repository

router = APIRouter(prefix="/api/v1")
admin = APIRouter(prefix="/admin", tags=["Administração"])


@router.get("/auth/first-password", response_model=s.FirstPasswordStatus, tags=["Conta"])
def first_password_status(identity: AuthenticatedSession):
    return s.FirstPasswordStatus(required=identity.first_password_required)


@router.put("/auth/first-password", status_code=204, tags=["Conta"])
def change_first_password(
    payload: s.FirstPasswordChange, identity: AuthenticatedSession, database: Data, auth: Auth,
):
    if not identity.first_password_required or not identity.email or not identity.password_version:
        raise HTTPException(409, "Não há uma troca de senha temporária pendente nesta sessão.")
    with database.session(identity.user_id, identity.aal) as connection:
        if not Repository(connection).lock_password_account(identity.email):
            raise HTTPException(409, "Há uma alteração em andamento. Tente novamente.")
        try:
            auth.complete_first_password(
                identity, payload.temporary_password.get_secret_value(),
                payload.new_password.get_secret_value(),
            )
        except PasswordActionRejected as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except InvalidSession as exc:
            raise HTTPException(401, "Entre novamente na sua conta.") from exc
        except AuthUnavailable as exc:
            raise HTTPException(503, "Autenticação indisponível.") from exc
    return Response(status_code=204)


@router.get("/auth/context", response_model=s.Context, tags=["Conta"])
def account_context(identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        context = repo.context()
        invitations = repo.pending_invitations(identity.email.lower()) if identity.email else []
    return {
        "user_id": identity.user_id,
        "email": identity.email,
        "aal": identity.aal,
        **context,
        "invitations": invitations,
    }


@router.post("/auth/invitations/{invitation_id}/accept", status_code=204, tags=["Conta"])
def accept_invitation(invitation_id: UUID, identity: CurrentUser, database: Data, auth: Auth):
    try:
        email = auth.confirmed_email(identity)
    except InvalidSession as exc:
        raise HTTPException(401, "Entre novamente na sua conta.") from exc
    except AuthUnavailable as exc:
        raise HTTPException(503, "Autenticação indisponível.") from exc
    if email is None:
        raise HTTPException(403, "Confirme seu e-mail para aceitar o convite.")
    with tenant_repository(database, identity) as repo:
        if not repo.lock_password_account(email):
            raise HTTPException(409, "Há uma alteração em andamento. Tente novamente.")
        if not repo.accept_invitation(invitation_id, email):
            raise HTTPException(404, "Convite indisponível.")
    return Response(status_code=204)


@router.get("/tenants/{tenant_id}/products", response_model=list[s.PortalProduct], tags=["Portal"])
def portal_products(tenant_id: UUID, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        return repo.tenant_products(tenant_id)


@admin.get("/clients", response_model=list[s.Client])
def list_clients(identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.access")
        return repo.list_organizations()


@admin.post("/clients", response_model=s.Client, status_code=201)
def create_client(payload: s.ClientCreate, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.tenant.manage")
        return repo.create_organization(payload.name, payload.slug)


@admin.patch("/clients/{tenant_id}", response_model=s.Client)
def update_client(tenant_id: UUID, payload: s.ClientUpdate, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "admin.tenant.manage")
        return resource(repo.update_organization(tenant_id, payload.name, payload.active))


@admin.get("/products", response_model=list[s.AdminProduct])
def list_products(identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.product.manage")
        return repo.list_products()


@admin.post("/products", response_model=s.AdminProduct, status_code=201)
def create_product(payload: s.ProductCreate, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.product.manage")
        return repo.create_product(
            payload.slug, payload.name, payload.description,
            offerable=payload.offerable, launch_url=payload.launch_url,
        )


@admin.patch("/products/{product_id}", response_model=s.AdminProduct)
def update_product(product_id: UUID, payload: s.ProductUpdate, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.product.manage")
        return resource(repo.update_product(
            product_id, payload.name, payload.description, payload.active,
            offerable=payload.offerable, launch_url=payload.launch_url,
            update_launch_url="launch_url" in payload.model_fields_set,
        ))


@admin.get("/clients/{tenant_id}/licenses", response_model=list[s.License])
def tenant_licenses(tenant_id: UUID, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "admin.entitlement.manage")
        resource(repo.get_organization(tenant_id))
        return repo.list_entitlements(tenant_id)


@admin.put("/clients/{tenant_id}/licenses/{product_id}", response_model=s.License)
def set_tenant_license(
    tenant_id: UUID, product_id: UUID, payload: s.LicenseUpdate, identity: CurrentUser, database: Data,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "admin.entitlement.manage")
        resource(repo.get_organization(tenant_id))
        return resource(repo.upsert_entitlement(
            tenant_id, product_id, payload.active, payload.starts_at, payload.ends_at,
        ))


@admin.get("/clients/{tenant_id}/users", response_model=s.Users)
@router.get("/tenants/{tenant_id}/users", response_model=s.Users, tags=["Portal"])
def tenant_users(tenant_id: UUID, identity: CurrentUser, database: Data, request: Request):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_users_access(repo, request, write=False)
        resource(repo.get_organization(tenant_id))
        return {
            "members": repo.list_memberships(tenant_id),
            "invitations": repo.list_invitations(tenant_id),
        }


@admin.post("/clients/{tenant_id}/users", response_model=s.Invitation, status_code=201)
@router.post("/tenants/{tenant_id}/users", response_model=s.Invitation, status_code=201, tags=["Portal"])
def invite_tenant_user(
    tenant_id: UUID,
    payload: s.InviteRequest,
    identity: CurrentUser,
    database: Data,
    auth: Auth,
    request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_users_access(repo, request, write=True)
        require_delegable_role(repo, payload.role)
        organization = resource(repo.get_organization(tenant_id))
        if not organization["active"]:
            raise HTTPException(409, "Reative a empresa antes de enviar convites.")
        if not repo.lock_password_account(payload.email):
            raise HTTPException(409, "Há uma alteração em andamento. Tente novamente.")
        invitation = resource(repo.create_invitation(tenant_id, payload.email, payload.role))
        if invitation["role"] != payload.role:
            raise HTTPException(409, "Já existe um convite pendente com outro perfil.")
        should_send = (
            invitation["delivery"] != "existing_account"
            and invitation["accepted_at"] is None
            and invitation["expires_at"] > datetime.now(UTC)
            and repo.claim_invitation_delivery(invitation["id"])
        )
    if not should_send:
        return invitation

    frontend_url = request.app.state.settings.frontend_url
    with tenant_repository(database, identity, tenant_id) as repo:
        if not repo.lock_password_account(payload.email):
            return invitation
        # O convite pode ter sido removido pela exclusão da conta entre as duas transações.
        if not repo.invitation_delivery_current(invitation["id"]):
            raise HTTPException(409, "O convite não está mais disponível. Atualize a página.")
        delivery = auth.send_invitation(payload.email, f"{frontend_url}/ativar-conta")
        return repo.record_invitation_delivery(invitation["id"], delivery) or invitation


@admin.post("/clients/{tenant_id}/users/temporary", response_model=s.Member, status_code=201)
def provision_temporary_member(
    tenant_id: UUID, payload: s.TemporaryMember,
    identity: CurrentUser, database: Data, auth: Auth,
):
    email = payload.email.lower()
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.tenant.manage")
        organization = resource(repo.get_organization(tenant_id))
        if not organization["active"]:
            raise HTTPException(409, "Reative a empresa antes de cadastrar usuários.")
        if not repo.lock_password_account(email):
            raise HTTPException(409, "Há uma alteração em andamento. Tente novamente.")
        candidate = repo.find_temporary_account(tenant_id, email)
        try:
            user_id = auth.provision_temporary_password(
                email, payload.temporary_password.get_secret_value(), tenant_id, existing=candidate,
            )
        except PasswordActionRejected as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except AuthUnavailable as exc:
            raise HTTPException(503, "Autenticação indisponível.") from exc
        if not repo.link_temporary_account(user_id, tenant_id, payload.role):
            raise HTTPException(409, "Não foi possível vincular a conta. Confira a empresa e tente novamente.")
    return s.Member(user_id=user_id, email=email, role=payload.role, active=True)


@admin.delete("/users/{user_id}", status_code=204)
def delete_portal_user(
    user_id: UUID, payload: s.UserDeletion, identity: CurrentUser, database: Data, auth: Auth,
):
    email = payload.email.lower()
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.tenant.manage")
        if user_id == identity.user_id:
            raise HTTPException(409, "Você não pode excluir a própria conta.")
        if not repo.lock_password_account(email):
            raise HTTPException(409, "Há uma alteração em andamento. Tente novamente.")
        current_email = resource(repo.portal_user_deletion_email(user_id))
        if current_email != email:
            raise HTTPException(409, "O e-mail de confirmação não confere com a conta.")
        try:
            auth.delete_user(user_id)
        except AuthUnavailable as exc:
            raise HTTPException(503, "Não foi possível confirmar a exclusão. Atualize a lista.") from exc
    return Response(status_code=204)


@admin.patch("/clients/{tenant_id}/users/{user_id}", response_model=s.Member)
@router.patch("/tenants/{tenant_id}/users/{user_id}", response_model=s.Member, tags=["Portal"])
def edit_tenant_user(
    tenant_id: UUID, user_id: UUID, payload: s.MemberUpdate,
    identity: CurrentUser, database: Data, request: Request,
):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_users_access(repo, request, write=True)
        require_delegable_role(repo, payload.role)
        return resource(repo.update_membership(tenant_id, user_id, payload.role, payload.active))


def require_users_access(repo: Repository, request: Request, *, write: bool) -> None:
    if request.url.path.startswith("/api/v1/admin/"):
        require_permission(repo, "admin.access")
    elif not repo.has_permission("tenant.access"):
        raise HTTPException(404, "Empresa indisponível.")
    require_permission(repo, "admin.users.manage" if write else "admin.users.read")


def require_delegable_role(repo: Repository, role: s.ClientRole) -> None:
    if role == "cliente_admin":
        require_permission(repo, "admin.tenant.manage")


def resource[T](value: T | None) -> T:
    if value is None:
        raise HTTPException(404, "Recurso indisponível.")
    return value


router.include_router(admin)
