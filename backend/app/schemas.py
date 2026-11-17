from typing import Annotated, Literal
from urllib.parse import urlsplit
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


class ProductFields(Input):
    name: Name
    launch_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)] | None = None

    @field_validator("launch_url")
    @classmethod
    def validate_launch_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https" or "." not in hostname
            or not hostname.rsplit(".", 1)[-1].isalpha()
            or hostname.endswith((".localhost", ".local", ".internal"))
            or parsed.username is not None or parsed.query or parsed.fragment
            or "\\" in value or any(character.isspace() for character in value)
        ):
            raise ValueError("Informe uma URL HTTPS pública, sem credenciais ou parâmetros.")
        return value


class ProductCreate(ProductFields):
    slug: Slug
    description: Description = ""
    offerable: bool = False


class ProductUpdate(ProductFields):
    description: Description
    active: bool
    offerable: bool | None = None


class PortalProduct(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str
    launch_url: str | None = None


class AdminProduct(PortalProduct):
    active: bool
    offerable: bool = False


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
