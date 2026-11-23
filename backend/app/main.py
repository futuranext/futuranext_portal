import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import SupabaseAuth
from .database import Database
from .first_access_email import ZohoFirstAccessMailer
from .modules.agenda import router as agenda_router
from .modules.asaas import AsaasClient, AsaasConfig
from .modules.billing import router as billing_router
from .modules.catalog import router as catalog_router
from .modules.fiscal import router as fiscal_router
from .modules.integrations import router as integrations_router
from .modules.integrations import webhook_router as integrations_webhook_router
from .modules.signup import router as signup_router
from .modules.site_chat import router as site_chat_router
from .modules.whatsapp import WhatsAppService
from .modules.whatsapp import router as whatsapp_router
from .routes import router
from .settings import Settings

logger = logging.getLogger("uvicorn.error")

PAGES = {
    "": "index.html",
    "login": "login.html",
    "recuperar-senha": "recuperar-senha.html",
    "ativar-conta": "ativar-conta.html",
    "cadastro": "cadastro.html",
    "portal": "portal.html",
    "portal/produtos": "portal/produtos.html",
    "portal/perfil": "portal/perfil.html",
    "portal/usuarios": "portal/usuarios.html",
    "portal/pagamento": "portal/pagamento.html",
    "portal/cobrancas": "portal/cobrancas.html",
    "admin": "admin.html",
    "admin/clientes": "admin/clientes.html",
    "admin/produtos": "admin/produtos.html",
    "admin/usuarios": "admin/usuarios.html",
    "admin/assinaturas": "admin/assinaturas.html",
    "admin/acessos": "admin/acessos.html",
    "admin/solicitacoes": "admin/solicitacoes.html",
    "admin/cobrancas": "admin/cobrancas.html",
}
LANDING_FILES = {
    "config.js", "script.js", "styles.css", "assets/logo-mark.svg", "assets/favicon.svg",
}


class RequestSizeLimit:
    def __init__(self, app: ASGIApp, limit: int = 64 * 1024) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return

        path = scope["path"].rstrip("/")
        limit = self.limit
        if (
            scope["method"] == "PUT"
            and path.startswith("/api/v1/tenants/")
            and path.endswith(("/tools/nfse/fiscal/certificate", "/tools/nfse-consulta/fiscal/certificate"))
        ):
            limit = 192 * 1024
        total = 0

        async def receive_limited():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise HTTPException(413, "O corpo da solicitação excede o limite.")
            return message

        await self.app(scope, receive_limited, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config.database_url.get_secret_value()) if config.database_url else None
        app.state.database = database
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            app.state.http = client
            if (
                config.zoho_mail_client_id and config.zoho_mail_client_secret
                and config.zoho_mail_refresh_token and config.zoho_mail_account_id
                and config.zoho_mail_sender
            ):
                app.state.first_access_mailer = ZohoFirstAccessMailer(
                    client, region=config.zoho_mail_region,
                    client_id=config.zoho_mail_client_id,
                    client_secret=config.zoho_mail_client_secret.get_secret_value(),
                    refresh_token=config.zoho_mail_refresh_token.get_secret_value(),
                    account_id=config.zoho_mail_account_id, sender_email=config.zoho_mail_sender,
                )
            if config.asaas_api_key:
                provider = AsaasConfig(
                    api_key=config.asaas_api_key.get_secret_value(),
                    environment=config.asaas_environment,
                    webhook_token=(
                        config.asaas_webhook_token.get_secret_value()
                        if config.asaas_webhook_token else None
                    ),
                )
                app.state.asaas = AsaasClient(client, provider)
            if config.whatsapp_service_url and config.whatsapp_service_token:
                app.state.whatsapp = WhatsAppService(
                    client, config.whatsapp_service_url,
                    config.whatsapp_service_token.get_secret_value(),
                )
            if config.supabase_url and config.supabase_issuer and config.supabase_publishable_key:
                app.state.auth = SupabaseAuth(
                    client,
                    config.supabase_url,
                    config.supabase_issuer,
                    config.supabase_publishable_key.get_secret_value(),
                    config.supabase_admin_key.get_secret_value() if config.supabase_admin_key else None,
                    config.auth_mode,
                )
            try:
                yield
            finally:
                if database:
                    database.engine.dispose()

    app = FastAPI(
        title="FuturaNext API",
        version="1.0.0",
        description="Identidade, empresas, usuários, catálogo e licenças da plataforma FuturaNext.",
        openapi_url="/openapi.json" if config.openapi_enabled else None,
        docs_url="/docs" if config.openapi_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.auth = None
    app.state.database = None
    app.state.asaas = None
    app.state.first_access_mailer = None
    app.state.whatsapp = None
    app.state.http = None
    app.add_middleware(RequestSizeLimit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        request_id = str(uuid4())
        start = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/health/")):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/api/"):
            route = request.scope.get("route")
            logger.info(json.dumps({
                "event": "api_request",
                "request_id": request_id,
                "method": request.method,
                "route": getattr(route, "path", None),
                "status": response.status_code,
                "elapsed_ms": round((perf_counter() - start) * 1000, 1),
            }))
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request: Request, error: RequestValidationError):
        errors = [{"location": item["loc"], "code": item["type"]} for item in error.errors()]
        return JSONResponse({"detail": "Confira os campos enviados.", "errors": errors}, 422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, error: SQLAlchemyError):
        code = getattr(getattr(error, "orig", None), "sqlstate", None)
        if isinstance(error, IntegrityError) and code in {"23503", "23505", "23514"}:
            return JSONResponse({"detail": "Os dados conflitam com o cadastro atual."}, 409)
        if code == "42501":
            return JSONResponse({"detail": "Ação não autorizada."}, 403)
        return JSONResponse({"detail": "Banco de dados indisponível."}, 503)

    @app.get("/health/live", tags=["Saúde"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Saúde"])
    def ready():
        database = app.state.database
        if app.state.auth is None or database is None or not database.ready():
            return JSONResponse({"status": "indisponivel"}, 503)
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(catalog_router)
    app.include_router(fiscal_router)
    app.include_router(agenda_router)
    app.include_router(billing_router)
    app.include_router(whatsapp_router)
    app.include_router(integrations_router)
    app.include_router(integrations_webhook_router)
    app.include_router(site_chat_router)
    app.include_router(signup_router)
    static = config.static_dir.resolve()
    csp = static_content_policy(config)

    @app.api_route("/{page:path}", methods=["GET", "HEAD"], response_class=FileResponse, include_in_schema=False)
    def public_file(page: str):
        allowed = page in LANDING_FILES or is_portal_asset(page)
        filename = page if allowed else PAGES.get(page.rstrip("/"))
        if filename:
            target = (static / filename).resolve()
            if target.is_relative_to(static) and target.is_file():
                return FileResponse(target, headers={"Content-Security-Policy": csp})
        not_found = static / "404.html"
        if not page.startswith(("api/", "health/")) and not_found.is_file():
            return FileResponse(not_found, status_code=404, headers={"Content-Security-Policy": csp})
        raise HTTPException(404, "Página indisponível.")

    return app


def is_portal_asset(page: str) -> bool:
    path = Path(page)
    return len(path.parts) == 2 and path.parts[0] == "platform" and path.suffix in {".css", ".js"}


def static_content_policy(config: Settings) -> str:
    connects = ["'self'"]
    if config.supabase_issuer:
        issuer = urlsplit(config.supabase_issuer)
        connects.append(f"{issuer.scheme}://{issuer.netloc}")
    return "; ".join((
        "default-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src " + " ".join(connects),
    ))


app = create_app()
