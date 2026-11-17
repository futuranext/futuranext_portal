"""E-mail transacional reutilizável via Zoho CPaaS. Nunca registra payload/segredo."""
from html import escape
import ipaddress
import re
from urllib.parse import urlsplit

import httpx
from .core import fail

EMAIL = r'^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$'


def site_url(config):
    url = config.consent_base_url.rstrip('/')
    parts = urlsplit(url)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
            or parts.password or parts.path or parts.query or parts.fragment):
        fail(503, 'SITE_URL_INVALID', 'Configure o endereço da plataforma.')
    return url


def email_ready(config):
    parts = urlsplit(site_url(config))
    host = parts.hostname or ''
    try:
        public = ipaddress.ip_address(host).is_global
    except ValueError:
        public = '.' in host and not host.endswith(('.localhost', '.local', '.test', '.invalid'))
    key = config.zoho_api_key.get_secret_value().strip()
    if key.lower().startswith('zoho-enczapikey '):
        key = key.split(' ', 1)[1].strip()
    return bool(parts.scheme == 'https' and public and key and re.fullmatch(EMAIL, config.email_from_address))


def require_email(config):
    if not email_ready(config):
        fail(503, 'EMAIL_NOT_CONFIGURED', 'O envio de e-mail ainda não está configurado. Contate o administrador.')


def link_message(title, explanation, url, button, footer):
    return (f'<html><body style="font-family:Arial,sans-serif;color:#20392b;padding:24px">'
            f'<h2>{escape(title)}</h2><p>{escape(explanation)}</p>'
            f'<p style="margin:32px 0"><a href="{escape(url, quote=True)}" '
            f'style="background:#187456;color:white;padding:16px 22px;border-radius:8px;text-decoration:none">'
            f'{escape(button)}</a></p><p>{escape(footer)}</p></body></html>')


async def send_email(config, *, recipient: str, subject: str, html: str, text: str, reference: str):
    """Uma tentativa. accepted é aceite pelo provedor, não entrega ao destinatário."""
    require_email(config)
    if not re.fullmatch(EMAIL, recipient):
        raise ValueError('E-mail de destino inválido.')
    key = config.zoho_api_key.get_secret_value().strip()
    if key.lower().startswith('zoho-enczapikey '):
        key = key.split(' ', 1)[1].strip()
    key = 'Zoho-enczapikey ' + key
    payload = {'from': {'address': config.email_from_address, 'name': config.email_from_name},
               'to': [{'email_address': {'address': recipient}}], 'subject': subject,
               'htmlbody': html, 'textbody': text, 'client_reference': reference,
               'track_opens': False, 'track_clicks': False}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post('https://cpaas.zoho.com/v1.1/email', json=payload,
                headers={'Authorization': key, 'Accept': 'application/json'})
        if 400 <= response.status_code < 500:
            return 'failed'
        if not response.is_success:
            return 'unknown'
        data = response.json()
        return 'accepted' if isinstance(data, dict) and data.get('status') == 'success' else 'unknown'
    except (httpx.HTTPError, ValueError):
        return 'unknown'
