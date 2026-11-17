import base64
import binascii
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12

MAX_CERTIFICATE_BYTES = 128 * 1024
MAX_CERTIFICATE_BASE64 = ((MAX_CERTIFICATE_BYTES + 2) // 3) * 4
MAX_PASSWORD_BYTES = 1024
ENVELOPE_VERSION = b"\x01"


class CertificateInputError(ValueError):
    pass


@dataclass(frozen=True)
class SealedCertificate:
    not_before: datetime
    not_after: datetime
    sealed_payload: bytes = field(repr=False)


def master_key(value: str | None) -> bytes | None:
    if not value:
        return None
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return None
    return decoded if len(decoded) == 32 else None


def certificate_aad(tenant_id: UUID, tool_slug: str) -> bytes:
    return b"futuranext:fiscal:a1:v1:" + tenant_id.bytes + b":" + tool_slug.encode("ascii")


def seal_certificate(
    encoded: str,
    password: str,
    key: bytes,
    tenant_id: UUID,
    tool_slug: str,
) -> SealedCertificate:
    if len(encoded) > MAX_CERTIFICATE_BASE64:
        raise CertificateInputError("O certificado excede o limite de 128 KiB.")
    try:
        payload = base64.b64decode(encoded, validate=True)
        password_bytes = password.encode("utf-8")
    except (ValueError, binascii.Error) as exc:
        raise CertificateInputError("Dados do certificado inválidos.") from exc
    if (
        not payload
        or len(payload) > MAX_CERTIFICATE_BYTES
        or len(password_bytes) > MAX_PASSWORD_BYTES
    ):
        raise CertificateInputError("O tamanho do arquivo ou da senha excede o limite.")
    try:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(
            payload, password_bytes or None
        )
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise CertificateInputError(
            "Não foi possível abrir o arquivo A1 com a senha informada."
        ) from exc
    if private_key is None or certificate is None:
        raise CertificateInputError(
            "O arquivo precisa conter o certificado A1 e sua chave privada."
        )
    key_public = private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    cert_public = certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    if key_public != cert_public:
        raise CertificateInputError("A chave privada não corresponde ao certificado.")
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        constraints = None
    except (ValueError, x509.DuplicateExtension) as exc:
        raise CertificateInputError("O certificado contém extensões X.509 inválidas.") from exc
    if constraints and constraints.ca:
        raise CertificateInputError(
            "Selecione um certificado de titular, sem função de autoridade."
        )
    now = datetime.now(UTC)
    if certificate.not_valid_before_utc > now:
        raise CertificateInputError("A validade do certificado ainda não começou.")
    if certificate.not_valid_after_utc <= now:
        raise CertificateInputError("O certificado está vencido.")

    nonce = secrets.token_bytes(12)
    plaintext = len(password_bytes).to_bytes(2, "big") + password_bytes + payload
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, certificate_aad(tenant_id, tool_slug))
    return SealedCertificate(
        certificate.not_valid_before_utc,
        certificate.not_valid_after_utc,
        ENVELOPE_VERSION + nonce + ciphertext,
    )
