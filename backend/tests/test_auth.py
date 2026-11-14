import json
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from app.auth import AuthUnavailable, InvalidSession, SupabaseAuth

ISSUER = "https://futuranext.test/auth/v1"
SUBJECT = str(uuid4())


def claims(**overrides):
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": "authenticated",
        "role": "authenticated",
        "sub": SUBJECT,
        "exp": now + 60,
        "iat": now,
        "aal": "aal1",
        "email": "conta@example.com",
        **overrides,
    }


@pytest.fixture
def jwks_auth():
    signing_key = ec.generate_private_key(ec.SECP256R1())
    key = json.loads(ECAlgorithm.to_jwk(signing_key.public_key()))
    key.update(kid="test-key", alg="ES256")

    def serve_jwks(request):
        assert request.url.path == "/auth/v1/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [key]})

    with httpx.Client(transport=httpx.MockTransport(serve_jwks)) as http:
        yield SupabaseAuth(http, "https://futuranext.test", ISSUER, "public", None, "jwks"), signing_key


def test_accepts_signed_session(jwks_auth):
    auth, key = jwks_auth
    token = jwt.encode(claims(aal="aal2"), key, algorithm="ES256", headers={"kid": "test-key"})
    identity = auth.identity(token)
    assert str(identity.user_id) == SUBJECT
    assert identity.aal == "aal2"
    assert token not in repr(identity)


@pytest.mark.parametrize("overrides", [
    {"iss": "https://outro-projeto.test/auth/v1"},
    {"aud": "service_role"},
    {"exp": 1},
    {"sub": "invalid-subject"},
    {"role": "service_role"},
    {"is_anonymous": True},
    {"aal": "unknown"},
])
def test_rejects_invalid_session_claims(jwks_auth, overrides):
    auth, key = jwks_auth
    token = jwt.encode(claims(**overrides), key, algorithm="ES256", headers={"kid": "test-key"})
    with pytest.raises(InvalidSession):
        auth.identity(token)


def test_rejects_signature_from_a_different_key(jwks_auth):
    auth, _ = jwks_auth
    wrong_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(claims(), wrong_key, algorithm="ES256", headers={"kid": "test-key"})
    with pytest.raises(InvalidSession):
        auth.identity(token)


@pytest.mark.parametrize("status, body, expected", [
    (200, {"id": SUBJECT}, None),
    (200, {"id": str(uuid4())}, InvalidSession),
    (401, {}, InvalidSession),
    (503, {}, AuthUnavailable),
])
def test_remote_validation_requires_matching_auth_user(status, body, expected):
    token = jwt.encode(claims(), b"x" * 32, algorithm="HS256")

    def serve_user(request):
        assert request.headers["authorization"] == f"Bearer {token}"
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(status, json=body)

    with httpx.Client(transport=httpx.MockTransport(serve_user)) as http:
        auth = SupabaseAuth(http, "https://futuranext.test", ISSUER, "public", None, "remote")
        if expected:
            with pytest.raises(expected):
                auth.identity(token)
        else:
            assert str(auth.identity(token).user_id) == SUBJECT


def test_invitation_preserves_retriable_provider_failure():
    statuses = iter([503, 422, 200])

    def auth_response(request):
        assert request.url.path == "/auth/v1/invite"
        assert request.url.params["redirect_to"] == "https://portal.test/ativar-conta"
        assert request.headers["apikey"] == "sb_secret_synthetic"
        status = next(statuses)
        return httpx.Response(status, json={"error_code": "email_exists"} if status == 422 else {})

    with httpx.Client(transport=httpx.MockTransport(auth_response)) as http:
        auth = SupabaseAuth(http, "https://futuranext.test", ISSUER, "public", "sb_secret_synthetic", "jwks")
        statuses = [auth.send_invitation("teste@example.com", "https://portal.test/ativar-conta") for _ in range(3)]
    assert statuses == ["pending", "existing_account", "sent"]
