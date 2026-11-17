from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .first_access_email import ZohoRegion


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FN_", env_file=".env", env_ignore_empty=True, extra="ignore",
    )

    environment: Literal["local", "test", "hml", "production"] = "local"
    database_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_issuer: str | None = None
    supabase_publishable_key: SecretStr | None = None
    supabase_admin_key: SecretStr | None = None
    fiscal_master_key: SecretStr | None = None
    asaas_environment: Literal["sandbox", "production"] = "sandbox"
    asaas_api_key: SecretStr | None = None
    asaas_webhook_token: SecretStr | None = None
    zoho_mail_region: ZohoRegion = "us"
    zoho_mail_client_id: str | None = None
    zoho_mail_client_secret: SecretStr | None = None
    zoho_mail_refresh_token: SecretStr | None = None
    zoho_mail_account_id: str | None = Field(default=None, pattern=r"^[0-9]{1,30}$")
    zoho_mail_sender: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    whatsapp_service_url: str | None = None
    whatsapp_service_token: SecretStr | None = None
    whatsapp_public_api_url: str | None = None
    auth_mode: Literal["jwks", "remote"] = "jwks"
    frontend_url: str = "http://localhost:8088"
    cors_origins: list[str] = []
    static_dir: Path = Path("dist")
    openapi_enabled: bool = True

    @model_validator(mode="after")
    def validate_deployment(self) -> "Settings":
        hosted = self.environment in {"hml", "production"}
        for name in ("supabase_url", "supabase_issuer", "frontend_url"):
            value = getattr(self, name)
            if value:
                setattr(self, name, self._validate_url(value, hosted, name == "supabase_issuer"))
        self.cors_origins = [self._validate_url(origin, hosted) for origin in self.cors_origins]
        if self.whatsapp_service_url:
            # Endereço interno da rede Docker; nunca recebe tráfego público.
            self.whatsapp_service_url = self._validate_url(self.whatsapp_service_url, False)
        if self.whatsapp_public_api_url:
            self.whatsapp_public_api_url = self._validate_url(
                self.whatsapp_public_api_url, hosted, allow_path=True,
            )
        if bool(self.whatsapp_service_url) != bool(self.whatsapp_service_token):
            raise ValueError("Configure o endereço e o token do serviço WhatsApp em conjunto.")
        if self.whatsapp_service_token and len(self.whatsapp_service_token.get_secret_value()) < 32:
            raise ValueError("O token do serviço WhatsApp precisa ter pelo menos 32 caracteres.")

        configured = (self.supabase_url, self.supabase_issuer, self.supabase_publishable_key)
        if any(configured) and not all(configured):
            raise ValueError("Configure URL, emissor e chave publicável do Supabase em conjunto.")
        if self.supabase_issuer and not self.supabase_issuer.endswith("/auth/v1"):
            raise ValueError("O emissor do Supabase deve terminar em /auth/v1.")
        if hosted and (not all(configured) or not self.database_url or not self.supabase_admin_key):
            raise ValueError("Configure o banco e as chaves do Supabase para este ambiente.")
        if hosted and self.auth_mode != "jwks":
            raise ValueError("Use uma chave de assinatura assimétrica em HML e produção.")
        return self

    @staticmethod
    def _validate_url(value: str, hosted: bool, allow_path: bool = False) -> str:
        parsed = urlsplit(value)
        schemes = {"https"} if hosted else {"http", "https"}
        if (
            parsed.scheme not in schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
            or "*" in value
            or any(char.isspace() for char in value)
            or (not allow_path and parsed.path not in {"", "/"})
        ):
            raise ValueError("URL inválida na configuração da plataforma.")
        return value.rstrip("/")
