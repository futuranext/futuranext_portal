from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel

from ..dependencies import CurrentUser, Data, require_permission, tenant_repository
from ..schemas import ClientRole, Input

router = APIRouter(prefix="/api/v1", tags=["Catálogo e acessos"])
SearchTerm = Annotated[str, Query(min_length=2, max_length=254)]


class Account(BaseModel):
    user_id: UUID
    email: str


class AccessSetting(Input):
    role: ClientRole
    granted: bool


class CompanyAccess(AccessSetting):
    tenant_id: UUID
    name: str
    company_active: bool


class Offer(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str
    pending: bool
    price_cents: int | None = None


class RequestStatus(BaseModel):
    id: UUID
    status: Literal["pending"] = "pending"


class ToolRequest(BaseModel):
    id: UUID
    organization: str
    product: str
    status: Literal["pending", "approved", "rejected"]
    created_at: AwareDatetime


class Review(Input):
    status: Literal["approved", "rejected"]


@router.get("/admin/access/users", response_model=list[Account])
def find_accounts(q: SearchTerm, identity: CurrentUser, database: Data):
    if len(q.strip()) < 2:
        raise HTTPException(422, "Informe pelo menos dois caracteres do e-mail.")
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.tenant.manage")
        return repo.search_accounts(q)


@router.get("/admin/access/users/{user_id}", response_model=list[CompanyAccess])
def account_access(user_id: UUID, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.tenant.manage")
        return repo.account_access(user_id)


@router.put("/admin/access/users/{user_id}/clients/{tenant_id}", status_code=204)
def update_access(
    user_id: UUID, tenant_id: UUID, payload: AccessSetting, identity: CurrentUser, database: Data,
):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.tenant.manage")
        if not repo.set_account_access(user_id, tenant_id, payload.role, payload.granted):
            raise HTTPException(409, "Confirme a conta e a situação da empresa antes de liberar o acesso.")
    return Response(status_code=204)


@router.get("/tenants/{tenant_id}/offers", response_model=list[Offer])
def read_offers(tenant_id: UUID, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        return repo.tool_offers(tenant_id)


@router.post("/tenants/{tenant_id}/offers/{product_id}/request", response_model=RequestStatus)
def request_offer(tenant_id: UUID, product_id: UUID, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity, tenant_id) as repo:
        require_permission(repo, "tenant.access", status=404)
        request_id = repo.request_tool(tenant_id, product_id)
        if request_id is None:
            raise HTTPException(404, "Oferta indisponível para esta empresa.")
        return RequestStatus(id=request_id)


@router.get("/admin/tool-requests", response_model=list[ToolRequest])
def list_requests(identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.entitlement.manage")
        return repo.list_tool_requests()


@router.put("/admin/tool-requests/{request_id}", status_code=204)
def review_request(request_id: UUID, payload: Review, identity: CurrentUser, database: Data):
    with tenant_repository(database, identity) as repo:
        require_permission(repo, "admin.entitlement.manage")
        if not repo.review_tool_request(request_id, payload.status):
            raise HTTPException(409, "A decisão já foi registrada ou a licença está indisponível.")
    return Response(status_code=204)
