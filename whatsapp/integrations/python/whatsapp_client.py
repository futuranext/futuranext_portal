"""Cliente da API própria; sem dependência do gateway ou de outro intermediário."""
import os
from uuid import uuid4
from urllib.parse import quote
import httpx


class WhatsAppClient:
    def __init__(self, base_url=None, api_key=None, instance_id=None):
        self.instance_id = instance_id or os.environ['WHATSAPP_INSTANCE_ID']
        self.http = httpx.Client(base_url=(base_url or os.environ['WHATSAPP_API_URL']).rstrip('/'),
            headers={'Authorization': 'Bearer ' + (api_key or os.environ['WHATSAPP_API_KEY'])}, timeout=30)

    def _request(self, method, path, **kwargs):
        response = self.http.request(method, '/api/v1' + path, **kwargs)
        response.raise_for_status()
        return response.json()

    def send_text(self, phone: str, message: str, idempotency_key: str | None = None):
        """Retorna o estado real. Guarde e reutilize a chave ao repetir o mesmo pedido."""
        return self._request('POST', '/messages/send', json={'instance_id': self.instance_id,
            'phone': phone, 'message': message, 'idempotency_key': idempotency_key or str(uuid4())})

    def get_status(self):
        return self._request('GET', f'/instances/{quote(self.instance_id, safe="")}/connection')

    def get_message_status(self, message_id: str):
        return self._request('GET', f'/messages/{quote(message_id, safe="")}')

    def send_image(self, *args, **kwargs):
        raise NotImplementedError('Envio de imagens será disponibilizado na Fase 4.')

    def send_document(self, *args, **kwargs):
        raise NotImplementedError('Envio de documentos será disponibilizado na Fase 4.')

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
