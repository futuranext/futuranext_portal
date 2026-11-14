import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal
from uuid import UUID

import httpx
import jwt

logger = logging.getLogger(__name__)

Delivery = Literal["sent", "existing_account", "pending"]
ASSURANCE_LEVELS = {"aal1", "aal2"}
ASYMMETRIC_ALGORITHMS = {"ES256", "RS256"}


class InvalidSession(Exception):
    pass


class AuthUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    email: str | None
    aal: Literal["aal1", "aal2"]
    token: str = field(repr=False)


class SupabaseAuth:
    def __init__(
        self,
        client: httpx.Client,
        url: str,
        issuer: str,
        publishable_key: str,
        admin_key: str | None,
        mode: Literal["jwks", "remote"],
    ) -> None:
        self.client = client
        self.auth_url = f"{url}/auth/v1"
        self.issuer = issuer
        self.publishable_key = publishable_key
        self.admin_key = admin_key
        self.mode = mode
        self._keys: dict[tuple[str, str], jwt.PyJWK] = {}
        self._key_observed_at = float("-inf")
        self._key_lock = Lock()

    def identity(self, token: str) -> Identity:
        if len(token) > 8192:
            raise InvalidSession
        try:
            header = jwt.get_unverified_header(token)
            claims = jwt.decode(token, options={"verify_signature": False})
            identity = self._session_claims(token, claims)
            if self.mode == "remote":
                if header.get("alg") not in ASYMMETRIC_ALGORITHMS | {"HS256"}:
                    raise InvalidSession
                self._user(identity)
            else:
                algorithm = header.get("alg")
                key_id = header.get("kid")
                if algorithm not in ASYMMETRIC_ALGORITHMS or not isinstance(key_id, str):
                    raise InvalidSession
                key = self._signing_key(key_id, algorithm)
                jwt.decode(
                    token, key, algorithms=[algorithm], issuer=self.issuer,
                    audience="authenticated", leeway=5,
                    options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise InvalidSession from exc
        return identity

    def _session_claims(self, token: str, claims: dict[str, Any]) -> Identity:
        audience = claims.get("aud")
        expiration = claims.get("exp")
        issued_at = claims.get("iat")
        not_before = claims.get("nbf", issued_at)
        now = time.time()
        aal = claims.get("aal")
        if (
            claims.get("iss") != self.issuer
            or not (
                audience == "authenticated"
                or (isinstance(audience, list) and "authenticated" in audience)
            )
            or claims.get("role") != "authenticated"
            or claims.get("is_anonymous", False) is not False
            or aal not in ASSURANCE_LEVELS
            or type(expiration) is not int
            or type(issued_at) is not int
            or type(not_before) is not int
            or expiration <= now - 5
            or issued_at > now + 5
            or not_before > now + 5
        ):
            raise InvalidSession
        subject = claims.get("sub")
        if not isinstance(subject, str):
            raise InvalidSession
        email = claims.get("email")
        return Identity(UUID(subject), email if isinstance(email, str) else None, aal, token)

    def _signing_key(self, key_id: str, algorithm: str) -> jwt.PyJWK:
        with self._key_lock:
            age = time.monotonic() - self._key_observed_at
            key = self._keys.get((key_id, algorithm))
            if key is not None and age < 300:
                return key
            if age < 5:
                raise InvalidSession
            response = self._request("GET", "/.well-known/jwks.json")
            if response.status_code != 200:
                raise AuthUnavailable
            try:
                raw_keys = response.json()["keys"]
                if not isinstance(raw_keys, list):
                    raise TypeError
                keys = [jwt.PyJWK.from_dict(raw) for raw in raw_keys]
            except (KeyError, ValueError, AttributeError, TypeError, jwt.PyJWTError) as exc:
                raise AuthUnavailable from exc
            self._keys = {
                (key.key_id, key.algorithm_name): key
                for key in keys
                if key.key_id and key.algorithm_name in ASYMMETRIC_ALGORITHMS
            }
            self._key_observed_at = time.monotonic()
            key = self._keys.get((key_id, algorithm))
            if key is None:
                raise InvalidSession
            return key

    def _user(self, identity: Identity) -> dict[str, Any]:
        response = self._request(
            "GET", "/user", headers={"Authorization": f"Bearer {identity.token}"},
        )
        if response.status_code in {400, 401, 403, 404}:
            raise InvalidSession
        if response.status_code != 200:
            raise AuthUnavailable
        try:
            user = response.json()
        except ValueError as exc:
            raise AuthUnavailable from exc
        if not isinstance(user, dict):
            raise AuthUnavailable
        if user.get("id") != str(identity.user_id) or user.get("is_anonymous", False):
            raise InvalidSession
        return user

    def confirmed_email(self, identity: Identity) -> str | None:
        user = self._user(identity)
        email = user.get("email")
        if user.get("email_confirmed_at") and isinstance(email, str):
            return email.lower()
        return None

    def send_invitation(self, email: str, redirect_url: str) -> Delivery:
        if not self.admin_key:
            logger.warning("auth_invite_unconfigured")
            return "pending"
        headers = {"apikey": self.admin_key}
        if not self.admin_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.admin_key}"
        try:
            response = self._request(
                "POST", "/invite", headers=headers, params={"redirect_to": redirect_url},
                json={"email": email},
            )
        except AuthUnavailable:
            logger.warning("auth_invite_unavailable")
            return "pending"
        if response.is_success:
            return "sent"
        error_code = response.headers.get("x-sb-error-code")
        if not error_code:
            try:
                body = response.json()
                error_code = body.get("error_code") if isinstance(body, dict) else None
            except ValueError:
                pass
        if response.status_code in {400, 422} and error_code in {"email_exists", "user_already_exists"}:
            return "existing_account"
        logger.warning("auth_invite_failed status=%s", response.status_code)
        return "pending"

    def _request(self, method: str, path: str, **options: Any) -> httpx.Response:
        headers = {"apikey": self.publishable_key, **options.pop("headers", {})}
        try:
            return self.client.request(method, f"{self.auth_url}{path}", headers=headers, **options)
        except httpx.RequestError as exc:
            raise AuthUnavailable from exc
