"""Aplicação FastAPI: middleware de segurança e módulos de negócio."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import internal, router
from .core import Runtime, Settings, new_id
from .portal_service import router as portal_router
from .providers import ProviderError

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = Runtime(Settings())
    app.state.rt = rt
    try:
        # O schema é versionado pelas migrações Alembic do portal (0013_whatsapp_service).
        await rt.open()
        yield
    finally:
        await rt.close()


_settings = Settings()
app = FastAPI(title='Plataforma WhatsApp — API própria', version='0.4.0', lifespan=lifespan,
              docs_url='/docs' if _settings.docs_enabled else None,
              openapi_url='/openapi.json' if _settings.docs_enabled else None, redoc_url=None)


def error_response(request, status, code, message, headers=None):
    return JSONResponse({'error': {'code': code, 'message': message,
        'request_id': getattr(request.state, 'request_id', '')}}, status_code=status, headers=headers)


@app.middleware('http')
async def security(request: Request, call_next):
    request.state.request_id = new_id()
    if request.method in ('POST', 'PUT', 'PATCH'):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1_048_576:
                return error_response(request, 413, 'BODY_TOO_LARGE', 'Requisição excede 1 MB.')
            chunks.append(chunk)
        request._body = b''.join(chunks)
    try:
        response = await call_next(request)
    except Exception:
        print(json.dumps({'level': 'error', 'event': 'request_failed', 'request_id': request.state.request_id}), flush=True)
        response = error_response(request, 503, 'SERVICE_UNAVAILABLE', 'Serviço temporariamente indisponível.')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Request-ID'] = request.state.request_id
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'REQUEST_ERROR', 'message': str(exc.detail)}
    return error_response(request, exc.status_code, detail['code'], detail['message'], exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return error_response(request, 422, 'VALIDATION_ERROR', 'Confira os campos informados e seus limites.')


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    return error_response(request, 503, exc.code, 'O gateway não respondeu como esperado. Tente novamente em instantes.')


@app.get('/health/live', tags=['Infraestrutura'])
async def live():
    return {'status': 'ok'}


async def dependencies_ready() -> bool:
    rt = app.state.rt
    async def gateway_ready():
        response = await rt.http.get('/', timeout=2)
        response.raise_for_status()
    try:
        async with asyncio.timeout(4):
            database, *_ = await asyncio.gather(rt.db.ready(), rt.redis.ping(), gateway_ready())
        return bool(database)
    except Exception:
        return False


@app.get('/health/ready', tags=['Infraestrutura'])
async def ready():
    ok = await dependencies_ready()
    return JSONResponse({'status': 'ready' if ok else 'unavailable'}, status_code=200 if ok else 503)


app.include_router(router)
app.include_router(internal)
app.include_router(portal_router)
