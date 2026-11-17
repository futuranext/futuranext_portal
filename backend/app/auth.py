import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal
from uuid import UUID, uuid4

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


class PasswordActionRejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    email: str | None
    aal: Literal["aal1", "aal2"]
    token: str = field(repr=False)
    first_password_required: bool = False
    password_version: str | None = None


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
        metadata = claims.get("app_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        version = metadata.get("futuranext_password_version")
        return Identity(
            UUID(subject), email if isinstance(email, str) else None, aal, token,
            metadata.get("futuranext_first_password") is True,
            str(UUID(version)) if isinstance(version, str) else None,
        )

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

    def _admin_headers(self) -> dict[str, str]:
        if not self.admin_key:
            raise AuthUnavailable
        headers = {"apikey": self.admin_key}
        if not self.admin_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.admin_key}"
        return headers

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        code = response.headers.get("x-sb-error-code")
        if code:
            return code
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload.get("error_code") if isinstance(payload, dict) else None

    @classmethod
    def _check_password_response(cls, response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code == 429:
            raise PasswordActionRejected(429, "Aguarde antes de tentar novamente.")
        if cls._error_code(response) in {"weak_password", "validation_failed"}:
            raise PasswordActionRejected(422, "Confira o e-mail e escolha uma senha mais forte.")
        raise AuthUnavailable

    def _has_verified_factor(self, user_id: UUID) -> bool:
        response = self._request("GET", f"/admin/users/{user_id}/factors", headers=self._admin_headers())
        if response.status_code != 200:
            raise AuthUnavailable
        try:
            factors = response.json()
        except ValueError as exc:
            raise AuthUnavailable from exc
        if not isinstance(factors, list):
            raise AuthUnavailable
        return any(isinstance(factor, dict) and factor.get("status") == "verified" for factor in factors)

    def provision_temporary_password(
        self, email: str, password: str, tenant_id: UUID, *, existing: UUID | None,
    ) -> UUID:
        metadata = {
            "futuranext_first_password": True,
            "futuranext_password_company": str(tenant_id),
            "futuranext_password_version": str(uuid4()),
        }
        payload = {"password": password, "email_confirm": True, "app_metadata": metadata}
        headers = self._admin_headers()
        if existing is None:
            response = self._request(
                "POST", "/admin/users", headers=headers, json={**payload, "email": email},
            )
            if not response.is_success and self._error_code(response) in {
                "email_exists", "user_already_exists",
            }:
                raise PasswordActionRejected(
                    409, "Já existe uma conta para este e-mail. Use Acessos por usuário ou o reset."
                )
        else:
            if self._has_verified_factor(existing):
                raise PasswordActionRejected(409, "A conta já tem um autenticador verificado.")
            response = self._request("PUT", f"/admin/users/{existing}", headers=headers, json=payload)
        self._check_password_response(response)
        try:
            user = response.json()
            user_id = UUID(user["id"])
            if user.get("email", "").lower() != email.lower() or (existing and user_id != existing):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AuthUnavailable from exc
        return user_id

    def complete_first_password(self, identity: Identity, temporary: str, password: str) -> None:
        user = self._user(identity)
        metadata = user.get("app_metadata")
        if (
            not isinstance(metadata, dict) or metadata.get("futuranext_first_password") is not True
            or metadata.get("futuranext_password_version") != identity.password_version
            or not identity.email or user.get("email", "").lower() != identity.email.lower()
        ):
            raise PasswordActionRejected(409, "Entre novamente para atualizar a configuração da conta.")
        if identity.aal != "aal2" and self._has_verified_factor(identity.user_id):
            raise PasswordActionRejected(403, "Conclua a verificação em duas etapas.")
        proof = self._request(
            "POST", "/token", params={"grant_type": "password"},
            json={"email": identity.email, "password": temporary},
        )
        if self._error_code(proof) == "invalid_credentials":
            raise PasswordActionRejected(400, "A senha temporária não confere.")
        self._check_password_response(proof)
        try:
            session = proof.json()
            proof_token = session["access_token"]
            proven_user = session["user"]
            if not isinstance(proof_token, str):
                raise TypeError
        except (KeyError, ValueError, TypeError) as exc:
            raise AuthUnavailable from exc
        # A atualização administrativa troca a senha e revoga todas as sessões do usuário.
        try:
            proven_metadata = proven_user.get("app_metadata") if isinstance(proven_user, dict) else None
            if (
                not isinstance(proven_metadata, dict) or proven_user.get("id") != str(identity.user_id)
                or proven_metadata.get("futuranext_first_password") is not True
                or proven_metadata.get("futuranext_password_version") != identity.password_version
            ):
                raise PasswordActionRejected(409, "Entre novamente para atualizar a configuração da conta.")
            response = self._request(
                "PUT", f"/admin/users/{identity.user_id}", headers=self._admin_headers(),
                json={"password": password, "app_metadata": {
                    "futuranext_first_password": False,
                    "futuranext_password_company": None,
                    "futuranext_password_version": str(uuid4()),
                }},
            )
            self._check_password_response(response)
        except (AuthUnavailable, PasswordActionRejected):
            try:
                self._request("POST", "/logout", params={"scope": "local"},
                              headers={"Authorization": f"Bearer {proof_token}"})
            except AuthUnavailable:
                pass
            raise

    def send_invitation(self, email: str, redirect_url: str) -> Delivery:
        try:
            response = self._request(
                "POST", "/invite", headers=self._admin_headers(), params={"redirect_to": redirect_url},
                json={"email": email},
            )
        except AuthUnavailable:
            logger.warning("auth_invite_unavailable")
            return "pending"
        if response.is_success:
            return "sent"
        error_code = self._error_code(response)
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
