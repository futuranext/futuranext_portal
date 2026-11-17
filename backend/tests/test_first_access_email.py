import json
from urllib.parse import parse_qs

import httpx

from app.first_access_email import ZohoFirstAccessMailer

FRONTEND = "https://futuranext.test"
RECIPIENT = "usuario@example.invalid"
TEMPORARY_PASSWORD = ' Espaço < "&$>  '


def make_mailer(client):
    return ZohoFirstAccessMailer(
        client,
        region="eu",
        client_id="synthetic-client",
        client_secret="synthetic-client-secret",
        refresh_token="synthetic-refresh-token",
        account_id="321",
        sender_email="suporte@futuranext.test",
    )


def send(mailer):
    return mailer.send(
        frontend_url=FRONTEND, recipient=RECIPIENT, temporary_password=TEMPORARY_PASSWORD,
    )


def test_sends_branded_first_access_email_and_reuses_oauth_token():
    calls = []

    def receive(request):
        calls.append(request)
        assert request.method == "POST"
        assert not request.url.query
        if request.url.path == "/oauth/v2/token":
            assert request.url.host == "accounts.zoho.eu"
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["refresh_token"],
                "refresh_token": ["synthetic-refresh-token"],
                "client_id": ["synthetic-client"],
                "client_secret": ["synthetic-client-secret"],
            }
            return httpx.Response(200, json={"access_token": "test-access", "expires_in": 3600})

        assert request.url.host == "mail.zoho.eu"
        assert request.url.path == "/api/accounts/321/messages"
        assert request.headers["authorization"] == "Zoho-oauthtoken test-access"
        body = json.loads(request.content)
        assert body["fromAddress"] == "suporte@futuranext.test"
        assert body["toAddress"] == RECIPIENT
        assert body["mailFormat"] == "html"
        assert "FuturaNext" in body["subject"]
        assert f'href="{FRONTEND}/login"' in body["content"]
        assert RECIPIENT in body["content"]
        assert "<code> Espaço &lt; &quot;&amp;$&gt;  </code>" in body["content"]
        assert "verificação em duas etapas" in body["content"]
        assert TEMPORARY_PASSWORD not in body["content"]
        return httpx.Response(200, json={"status": {"code": 200}})

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        mailer = make_mailer(client)
        assert send(mailer)
        assert send(mailer)
    assert len(calls) == 3


def test_send_does_not_retry_or_log_sensitive_provider_response(caplog):
    calls = []

    def receive(request):
        calls.append(request.url.path)
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "test-access", "expires_in": 3600})
        return httpx.Response(200, json={
            "status": {"code": 500}, "data": {"content": TEMPORARY_PASSWORD},
        })

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        assert not send(make_mailer(client))
    assert calls == ["/oauth/v2/token", "/api/accounts/321/messages"]
    assert TEMPORARY_PASSWORD not in caplog.text


def test_refuses_recipient_lists_before_contacting_zoho():
    def unexpected_request(_request):
        raise AssertionError("A senha não pode ser enviada para uma lista.")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        assert not make_mailer(client).send(
            frontend_url=FRONTEND, recipient="user,other@example.invalid",
            temporary_password=TEMPORARY_PASSWORD,
        )
