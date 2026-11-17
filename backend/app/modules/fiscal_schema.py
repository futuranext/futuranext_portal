import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .fiscal_crypto import MAX_CERTIFICATE_BASE64, MAX_PASSWORD_BYTES

ToolSlug = Literal["nfse", "nfse-consulta"]
FiscalEnvironment = Literal["restrita", "producao"]
CertificateStatus = Literal["ausente", "vigente", "a_vencer", "vencido", "a_vigorar"]


def normalize_cnpj(value: str) -> str:
    compact = re.sub(r"[./\s-]", "", value)
    if not re.fullmatch(r"[A-Za-z0-9]{12}[0-9]{2}", compact):
        raise ValueError("Informe um CNPJ válido.")
    number = compact.upper()
    if len(set(number)) == 1:
        raise ValueError("Informe um CNPJ válido.")
    body = number[:12]
    for weights in (
        (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
        (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
    ):
        remainder = (
            sum((ord(char) - 48) * weight for char, weight in zip(body, weights, strict=True)) % 11
        )
        body += str(11 - remainder if remainder >= 2 else 0)
    if body != number:
        raise ValueError("Os dígitos verificadores do CNPJ não conferem.")
    return body


class FiscalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmissionSettings(FiscalModel):
    environment: FiscalEnvironment = "restrita"
    issuer_cnpj: str = Field(max_length=24)
    municipal_registration: str = Field(default="", max_length=30)
    municipality_ibge: str = Field(pattern=r"^[0-9]{7}$")
    national_tax_code: str = Field(pattern=r"^[0-9]{6}$")
    municipal_tax_code: str = Field(default="", pattern=r"^(?:[0-9]{3})?$")
    nbs_code: str = Field(default="", pattern=r"^(?:[0-9]{9})?$")
    dps_series: str = Field(default="1", pattern=r"^[0-9]{1,5}$")
    service_description: str = Field(default="", max_length=500)

    @field_validator("issuer_cnpj")
    @classmethod
    def normalize_issuer(cls, value: str) -> str:
        return normalize_cnpj(value)


class ConsultationCompany(FiscalModel):
    cnpj: str = Field(max_length=24)
    name: str = Field(default="", max_length=100)
    active: bool = True

    @field_validator("cnpj")
    @classmethod
    def normalize_company(cls, value: str) -> str:
        return normalize_cnpj(value)


class ConsultationSettings(FiscalModel):
    environment: FiscalEnvironment = "restrita"
    companies: list[ConsultationCompany] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_companies(self) -> "ConsultationSettings":
        cnpjs = [company.cnpj for company in self.companies]
        if len(cnpjs) != len(set(cnpjs)):
            raise ValueError("Cada CNPJ só pode aparecer uma vez.")
        return self


class CertificateUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: SecretStr = Field(min_length=1, max_length=MAX_CERTIFICATE_BASE64)
    password: SecretStr = Field(max_length=MAX_PASSWORD_BYTES)


class CertificateView(FiscalModel):
    status: CertificateStatus
    not_before: datetime | None = None
    not_after: datetime | None = None
    updated_at: datetime | None = None
    days_remaining: int | None = None


class FiscalView(FiscalModel):
    tool_slug: ToolSlug
    settings: EmissionSettings | ConsultationSettings | None
    certificate: CertificateView
    encryption_configured: bool
    mfa_verified: bool
