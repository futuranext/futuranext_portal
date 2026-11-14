from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

from .auth import Delivery

ClientRole = Literal["cliente_admin", "cliente_usuario"]
PlatformRole = Literal["super_admin", "suporte"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Slug = Annotated[str, StringConstraints(min_length=2, max_length=63, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254,
                                         pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientCreate(Input):
    name: Name
    slug: Slug


class ClientUpdate(Input):
    name: Name
    active: bool


class Client(BaseModel):
    id: UUID
    name: str
    slug: str
    active: bool


class MembershipContext(BaseModel):
    id: UUID
    name: str
    slug: str
    role: ClientRole


class ProductCreate(Input):
    name: Name
    slug: Slug
    description: Description = ""


class ProductUpdate(Input):
    name: Name
    description: Description
    active: bool


class PortalProduct(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str


class AdminProduct(PortalProduct):
    active: bool


class LicenseUpdate(Input):
    active: bool
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def order_dates(self) -> "LicenseUpdate":
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("O fim da licença deve ser posterior ao início.")
        return self


class License(BaseModel):
    product_id: UUID
    active: bool
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None


class InviteRequest(Input):
    email: Email
    role: ClientRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        return email.lower()


class Invitation(BaseModel):
    id: UUID
    email: str
    role: ClientRole
    expires_at: AwareDatetime
    accepted_at: AwareDatetime | None
    delivery: Delivery


class PendingInvitation(BaseModel):
    id: UUID
    organization_name: str
    role: ClientRole
    expires_at: AwareDatetime


class Context(BaseModel):
    user_id: UUID
    email: str | None
    aal: Literal["aal1", "aal2"]
    platform_role: PlatformRole | None
    organizations: list[MembershipContext]
    invitations: list[PendingInvitation]


class MemberUpdate(Input):
    role: ClientRole
    active: bool


class Member(BaseModel):
    user_id: UUID
    email: str | None
    role: ClientRole
    active: bool


class Users(BaseModel):
    members: list[Member]
    invitations: list[Invitation]
