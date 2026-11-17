"""Adapter Asaas: destinos fixos, sem repetição de mutações ou dados de cartão."""
import httpx
from urllib.parse import urlparse


class AsaasError(Exception):
    def __init__(self, status=None):
        self.status = status


def ready(config):
    return bool(config.asaas_api_key.get_secret_value().strip()) and config.asaas_enabled


def invoice_url(value):
    url = urlparse(value or '')
    return value if url.scheme == 'https' and url.hostname in ('www.asaas.com', 'asaas.com', 'sandbox.asaas.com') and not url.username else None


async def call(config, method, path, payload=None):
    if not ready(config):
        raise AsaasError()
    host = 'api.asaas.com' if config.asaas_environment == 'production' else 'api-sandbox.asaas.com'
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.request(method, f'https://{host}/v3{path}',
                headers={'access_token': config.asaas_api_key.get_secret_value(),
                         'User-Agent': 'CentralWhatsApp/0.4', 'Accept': 'application/json'},
                **({'json': payload} if payload is not None else {}))
        if not response.is_success:
            raise AsaasError(response.status_code)
        data = response.json()
        if not isinstance(data, dict):
            raise AsaasError()
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise AsaasError() from exc
