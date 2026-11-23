import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from .auth import Delivery
from .modules.fiscal_schema import normalize_cnpj

ClientRole = Literal["cliente_admin", "cliente_usuario"]
PlatformRole = Literal["super_admin", "suporte"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Slug = Annotated[str, StringConstraints(min_length=2, max_length=63, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254,
                                         pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


def normalize_document(value: str) -> str:
    """CPF ou CNPJ (numérico ou alfanumérico) com dígitos verificadores."""
    compact = re.sub(r"[./\s-]", "", value).upper()
    if len(compact) != 11:
        return normalize_cnpj(compact)
    if not compact.isdigit() or len(set(compact)) == 1:
        raise ValueError("Informe um CPF ou CNPJ válido.")
    body = compact[:9]
    for size in (10, 11):
        remainder = sum(int(digit) * weight for digit, weight in zip(body, range(size, 1, -1))) * 10 % 11
        body += str(remainder % 10)
    if body != compact:
        raise ValueError("Os dígitos verificadores do CPF não conferem.")
    return compact


def normalize_phone(value: str) -> str:
    """Telefone brasileiro com DDD, aceitando +55 e formatação."""
    digits = re.sub(r"[\s().+-]", "", value)
    if len(digits) in {12, 13} and digits.startswith("55"):
        digits = digits[2:]
    if not re.fullmatch(r"[1-9][0-9]{9,10}", digits) or (len(digits) == 11 and digits[2] != "9"):
        raise ValueError("Informe o telefone com DDD, por exemplo (11) 91234-5678.")
    return digits


Document = Annotated[str, StringConstraints(strip_whitespace=True, min_length=11, max_length=24),
                     AfterValidator(normalize_document)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=24),
                  AfterValidator(normalize_phone)]
BillingEmail = Annotated[Email, AfterValidator(str.lower)]


def validate_password(value: SecretStr) -> SecretStr:
    password = value.get_secret_value()
    if len(password) < 12 or len(password.encode("utf-8")) > 72 or "\x00" in password:
        raise ValueError("Use pelo menos 12 caracteres respeitando o limite de 72 bytes.")
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientBilling(Input):
    """Campos exigidos pelo Asaas para criar o cliente de cobrança."""

    document: Document
    billing_email: BillingEmail
    billing_phone: Phone


class ClientCreate(ClientBilling):
    name: Name
    slug: Slug


class TemporaryMember(Input):
    email: Email
    role: ClientRole
    temporary_password: SecretStr

    _validate_password = field_validator("temporary_password")(validate_password)


class UserDeletion(Input):
    email: Email


class FirstPasswordChange(Input):
    temporary_password: SecretStr
    new_password: SecretStr

    _validate_password = field_validator("temporary_password", "new_password")(validate_password)

    @model_validator(mode="after")
    def check_changed(self) -> "FirstPasswordChange":
        if self.temporary_password == self.new_password:
            raise ValueError("Escolha uma senha diferente da temporária.")
        return self


class FirstPasswordStatus(BaseModel):
    required: bool


class ClientUpdate(ClientBilling):
    name: Name
    active: bool


BillingCustomerStatus = Literal[
    "linked", "incomplete", "no_access", "inactive", "unavailable", "rejected", "failed", "busy",
]


class Client(BaseModel):
    id: UUID
    name: str
    slug: str
    active: bool
    document: str | None = None
    billing_email: str | None = None
    billing_phone: str | None = None
    billing_customer: BillingCustomerStatus | None = None


class MembershipContext(BaseModel):
    id: UUID
    name: str
    slug: str
    role: ClientRole


Price = Annotated[int, Field(strict=True, ge=1, le=10_000_000)]


class ProductFields(Input):
    name: Name
    # Mensalidade em centavos. Sem preço, a ferramenta não pode ser comprada pelo portal.
    price_cents: Price | None = None
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
    price_cents: int | None = None


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


class TemporaryMemberCreated(Member):
    email_delivery: Literal["sent", "unavailable", "failed"]


class Users(BaseModel):
    members: list[Member]
    invitations: list[Invitation]
