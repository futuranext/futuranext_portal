import logging
import time
from html import escape
from threading import Lock
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

ZohoRegion = Literal["us", "eu", "in", "au", "jp", "ca", "cn", "ae", "sa"]
REGIONAL_DOMAINS: dict[ZohoRegion, str] = {
    "us": "zoho.com",
    "eu": "zoho.eu",
    "in": "zoho.in",
    "au": "zoho.com.au",
    "jp": "zoho.jp",
    "ca": "zohocloud.ca",
    "cn": "zoho.com.cn",
    "ae": "zoho.ae",
    "sa": "zoho.sa",
}
REQUEST_TIMEOUT = 4.0
SUBJECT = "Seu primeiro acesso à FuturaNext"


def first_access_html(frontend_url: str, recipient: str, temporary_password: str) -> str:
    login_url = escape(f"{frontend_url.rstrip('/')}/login")
    username = escape(recipient)
    password = escape(temporary_password).replace("\r", "&#13;")
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SUBJECT}</title>
</head>
<body style="margin:0;background:#f3f7f3;color:#102838;font-family:Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f3f7f3">
    <tr><td align="center" style="padding:24px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
        style="max-width:560px;border:1px solid #d8e3de;border-radius:12px;">
        <tr><td bgcolor="#081a2a"
          style="padding:20px 24px;border-radius:11px 11px 0 0;color:#fff;font-size:22px;font-weight:bold;">
          Futura<span style="color:#08ce88;">Next</span>
        </td></tr>
        <tr><td style="padding:28px 24px 30px;font-size:16px;line-height:1.55;">
          <h1 style="margin:0 0 16px;font-size:26px;line-height:1.25;">Seu primeiro acesso</h1>
          <p style="margin:0 0 20px;">Sua conta no portal da FuturaNext está pronta.</p>
          <p style="margin:0 0 8px;">Usuário <strong>{username}</strong></p>
          <p style="margin:0;">Senha temporária</p>
          <pre style="margin:4px 0 20px;padding:12px;background:#f3f7f3;border-radius:6px;
            font-family:monospace;white-space:pre-wrap;overflow-wrap:anywhere;"><code>{password}</code></pre>
          <table role="presentation" cellpadding="0" cellspacing="0">
            <tr><td bgcolor="#08ce88" style="border-radius:8px;mso-padding-alt:14px 22px;">
              <a href="{login_url}" style="display:inline-block;padding:14px 22px;color:#062d22;
                font-weight:bold;line-height:1.25;text-decoration:none;">Acessar o portal</a>
            </td></tr>
          </table>
          <p style="margin:20px 0 0;">No primeiro acesso, troque a senha temporária e configure
            a verificação em duas etapas no aplicativo autenticador.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


class ZohoFirstAccessMailer:
    def __init__(
        self,
        client: httpx.Client,
        *,
        region: ZohoRegion,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        account_id: str,
        sender_email: str,
    ) -> None:
        self._client = client
        domain = REGIONAL_DOMAINS[region]
        self._token_url = f"https://accounts.{domain}/oauth/v2/token"
        self._send_url = f"https://mail.{domain}/api/accounts/{account_id}/messages"
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._sender_email = sender_email
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = Lock()

    def _access_token(self) -> str | None:
        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token

            response = self._client.post(
                self._token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=REQUEST_TIMEOUT,
                follow_redirects=False,
            )
            if not response.is_success:
                logger.warning("first_access_email_auth_rejected status=%s", response.status_code)
                return None

            payload = response.json()
            token = payload.get("access_token") if isinstance(payload, dict) else None
            expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
            if not (
                isinstance(token, str) and 0 < len(token) <= 8192
                and all("!" <= char <= "~" for char in token)
                and type(expires_in) is int and expires_in > 0
            ):
                logger.warning("first_access_email_auth_invalid")
                return None

            self._token = token
            self._token_expires = time.monotonic() + min(expires_in, 3600) - 30
            return token

    def send(self, *, frontend_url: str, recipient: str, temporary_password: str) -> bool:
        return self.deliver(
            recipient=recipient, subject=SUBJECT,
            html=first_access_html(frontend_url, recipient, temporary_password),
        )

    def deliver(self, *, recipient: str, subject: str, html: str) -> bool:
        # O provedor aceita listas em toAddress. Cada mensagem só pode ir para uma conta.
        if any(separator in recipient for separator in ",;"):
            logger.warning("first_access_email_invalid_recipient")
            return False
        try:
            token = self._access_token()
            if token is None:
                return False
            response = self._client.post(
                self._send_url,
                headers={"Authorization": f"Zoho-oauthtoken {token}"},
                json={
                    "fromAddress": self._sender_email,
                    "toAddress": recipient,
                    "subject": subject,
                    "mailFormat": "html",
                    "content": html,
                },
                timeout=REQUEST_TIMEOUT,
                follow_redirects=False,
            )
            if response.status_code == 401:
                with self._token_lock:
                    if self._token == token:
                        self._token = None

            if response.is_success:
                payload = response.json()
                status = payload.get("status") if isinstance(payload, dict) else None
                if isinstance(status, dict) and status.get("code") == 200:
                    return True
            logger.warning("first_access_email_send_unconfirmed status=%s", response.status_code)
        except (httpx.RequestError, ValueError):
            logger.warning("first_access_email_unavailable")
        return False
