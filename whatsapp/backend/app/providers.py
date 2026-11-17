"""Contrato de provedor e adapter Evolution 2.3.7; sem credenciais nas respostas."""
from abc import ABC, abstractmethod
from urllib.parse import quote
import httpx


class ProviderError(Exception):
    def __init__(self, code: str, uncertain: bool = False, retryable: bool = False):
        self.code, self.uncertain, self.retryable = code, uncertain, retryable
        super().__init__(code)


class WhatsAppProvider(ABC):
    capabilities = {'qr', 'text'}

    @abstractmethod
    async def create(self, reference: str, webhook_url: str, webhook_secret: str): ...
    @abstractmethod
    async def connect(self, reference: str): ...
    @abstractmethod
    async def connection(self, reference: str): ...
    @abstractmethod
    async def disconnect(self, reference: str): ...
    @abstractmethod
    async def delete(self, reference: str): ...
    @abstractmethod
    async def send_text(self, reference: str, phone: str, message: str): ...


class EvolutionProvider(WhatsAppProvider):
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def request(self, method: str, path: str, payload=None, sending=False):
        try:
            response = await self.http.request(method, path, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ProviderError('GATEWAY_UNAVAILABLE', retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError('GATEWAY_TIMEOUT', uncertain=sending) from None
        if response.status_code >= 400:
            raise ProviderError(f'GATEWAY_HTTP_{response.status_code}',
                                uncertain=sending and response.status_code >= 500)
        try:
            return response.json()
        except ValueError:
            raise ProviderError('GATEWAY_INVALID_RESPONSE', uncertain=sending) from None

    @staticmethod
    def ref(reference: str) -> str:
        return quote(reference, safe='')

    async def create(self, reference, webhook_url, webhook_secret):
        return await self.request('POST', '/instance/create', {
            'instanceName': reference, 'integration': 'WHATSAPP-BAILEYS', 'qrcode': False,
            'groupsIgnore': True, 'readMessages': False, 'readStatus': False, 'syncFullHistory': False,
            'webhook': {'enabled': True, 'url': webhook_url, 'byEvents': False, 'base64': False,
                'headers': {'x-webhook-secret': webhook_secret},
                'events': ['QRCODE_UPDATED', 'CONNECTION_UPDATE', 'MESSAGES_UPSERT', 'MESSAGES_UPDATE']}})

    async def connect(self, reference):
        return await self.request('GET', f'/instance/connect/{self.ref(reference)}')

    async def connection(self, reference):
        data = await self.request('GET', f'/instance/connectionState/{self.ref(reference)}')
        return connection_state(data.get('instance', {}).get('state'))

    async def disconnect(self, reference):
        return await self.request('DELETE', f'/instance/logout/{self.ref(reference)}')

    async def delete(self, reference):
        try:
            return await self.request('DELETE', f'/instance/delete/{self.ref(reference)}')
        except ProviderError as exc:
            if exc.code != 'GATEWAY_HTTP_404':
                raise

    async def send_text(self, reference, phone, message):
        return await self.request('POST', f'/message/sendText/{self.ref(reference)}',
                                  {'number': phone, 'text': message, 'linkPreview': False}, sending=True)


def connection_state(state):
    return {'open': 'connected', 'close': 'disconnected', 'connecting': 'connecting',
            'refused': 'expired'}.get(state, 'disconnected')
