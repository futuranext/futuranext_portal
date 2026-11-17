from base64 import b64encode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from fastapi import Request
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_postgres import platform  # noqa: F401

from app.auth import Identity
from app.dependencies import authenticate
from app.main import create_app
from app.modules.fiscal_crypto import (
    CertificateInputError,
    certificate_aad,
    master_key,
    seal_certificate,
)
from app.modules.fiscal_schema import normalize_cnpj
from app.modules.fiscal_template import COLUMNS, emission_template, legacy_ccp_template
from app.repository import Repository
from app.settings import Settings

SYNTHETIC_PASSWORD = "senha-exclusiva-de-fixture"


def synthetic_certificate(*, starts: int = -1, ends: int = 15) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fixture fiscal local")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=starts))
        .not_valid_after(now + timedelta(days=ends))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"fixture-fiscal", key, cert, None, BestAvailableEncryption(SYNTHETIC_PASSWORD.encode())
    )


def test_cnpj_accepts_numeric_and_official_alphanumeric_examples():
    assert normalize_cnpj("12.345.678/0001-95") == "12345678000195"
    assert normalize_cnpj("12.abc.345/01de-35") == "12ABC34501DE35"
    for invalid in ("12.abc.345/01de-34", "11.111.111/1111-11", "12.ábç.345/01de-35"):
        with pytest.raises(ValueError):
            normalize_cnpj(invalid)


def test_certificate_seal_contains_file_and_password_with_tenant_and_tool_binding():
    tenant_id, other_tenant = uuid4(), uuid4()
    key = AESGCM.generate_key(bit_length=256)
    assert master_key(urlsafe_b64encode(key).decode().rstrip("=")) == key
    assert master_key("invalid") is None
    document = synthetic_certificate()
    encoded = b64encode(document).decode()
    first = seal_certificate(encoded, SYNTHETIC_PASSWORD, key, tenant_id, "nfse")
    second = seal_certificate(encoded, SYNTHETIC_PASSWORD, key, tenant_id, "nfse")
    assert first.sealed_payload != second.sealed_payload
    assert first.not_after > first.not_before

    nonce, ciphertext = first.sealed_payload[1:13], first.sealed_payload[13:]
    cipher = AESGCM(key)
    clear = cipher.decrypt(nonce, ciphertext, certificate_aad(tenant_id, "nfse"))
    password_length = int.from_bytes(clear[:2], "big")
    assert clear[2 : 2 + password_length].decode() == SYNTHETIC_PASSWORD
    assert clear[2 + password_length :] == document
    for cross_tenant, cross_tool in ((other_tenant, "nfse"), (tenant_id, "nfse-consulta")):
        with pytest.raises(InvalidTag):
            cipher.decrypt(nonce, ciphertext, certificate_aad(cross_tenant, cross_tool))


def test_certificate_invalid_password_and_validity():
    key = AESGCM.generate_key(bit_length=256)
    tenant = uuid4()
    valid = b64encode(synthetic_certificate()).decode()
    expired = b64encode(synthetic_certificate(starts=-10, ends=-2)).decode()
    future = b64encode(synthetic_certificate(starts=2, ends=15)).decode()
    for sample, password, error in (
        (valid, "wrong-password", "senha"),
        (expired, SYNTHETIC_PASSWORD, "vencido"),
        (future, SYNTHETIC_PASSWORD, "ainda não começou"),
        ("=&", SYNTHETIC_PASSWORD, "inválidos"),
    ):
        with pytest.raises(CertificateInputError, match=error):
            seal_certificate(sample, password, key, tenant, "nfse")


def test_workbook_is_versioned_and_has_only_blank_emission_rows():
    assert emission_template().startswith(b"PK\x03\x04")
    workbook = load_workbook(BytesIO(emission_template()))
    assert workbook.sheetnames == ["Instruções", "Emissão"]
    assert workbook["Instruções"]["B1"].value == "Versão 1.0"
    instructions = workbook["Emissão"]
    assert tuple(cell.value for cell in instructions[1]) == tuple(column[0] for column in COLUMNS)
    assert all(
        cell is None for row in instructions.iter_rows(min_row=2, values_only=True) for cell in row
    )
    assert instructions.column_dimensions["B"].number_format == "@"


def test_ccp_workbook_follows_the_legacy_import_contract():
    workbook = load_workbook(BytesIO(legacy_ccp_template()))
    assert workbook["Instruções"]["B1"].value == "Versão 1.0"
    sheet = workbook["Base"]
    assert tuple(cell.value for cell in sheet[1]) == (
        "Aluno",
        "Nome Aluno",
        "Nome Responsável",
        "Data Vencimento",
        "CPF Responsável",
        "Numero Endereco",
        "CEP",
        "Vlr Recebido",
        "NF",
        "Status",
        "NBS",
    )
    assert all(
        value is None for row in sheet.iter_rows(min_row=2, values_only=True) for value in row
    )
    assert sheet.column_dimensions["E"].number_format == "@"
    assert sheet.column_dimensions["G"].number_format == "@"
    assert sheet["D2"].number_format == "dd/mm/yyyy"
    assert sheet["H2"].number_format == "0.00"


def test_fiscal_http_enforces_licensing_roles_mfa_and_certificate_acl(platform):  # noqa: F811
    tenant_a, tenant_b = platform.tenant_a, platform.tenant_b
    with platform.database.session(platform.admin, "aal2") as connection:
        products = {
            product["slug"]: product["id"] for product in Repository(connection).list_products()
        }
    for tenant, tools in ((tenant_a, ("nfse", "nfse-consulta")), (tenant_b, ("nfse-consulta",))):
        with platform.database.session(platform.admin, "aal2", tenant) as connection:
            repo = Repository(connection)
            for tool in tools:
                repo.upsert_entitlement(tenant, products[tool], True, None, None)

    key = urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()
    app = create_app(Settings(_env_file=None, fiscal_master_key=key))

    def synthetic_identity(request: Request) -> Identity:
        user = platform.alice if request.headers.get("x-fixture-user") == "alice" else platform.bob
        level = "aal2" if request.headers.get("x-fixture-mfa") else "aal1"
        return Identity(user, None, level, "synthetic-not-a-token")

    app.dependency_overrides[authenticate] = synthetic_identity
    alice = {"x-fixture-user": "alice"}
    verified = {**alice, "x-fixture-mfa": "true"}
    emission = f"/api/v1/tenants/{tenant_a}/tools/nfse/fiscal"
    query_a = f"/api/v1/tenants/{tenant_a}/tools/nfse-consulta/fiscal"
    query_b = f"/api/v1/tenants/{tenant_b}/tools/nfse-consulta/fiscal"
    settings = {"companies": [{"cnpj": "12.abc.345/01de-35", "name": "Emissor sintético"}]}
    payload = {
        "content_base64": b64encode(synthetic_certificate()).decode(),
        "password": SYNTHETIC_PASSWORD,
    }
    with TestClient(app) as client:
        app.state.database = platform.database
        assert client.get(query_b, headers=verified).status_code == 404
        assert client.get(query_b).status_code == 200
        assert client.put(query_b + "/settings", json=settings).status_code == 403
        assert client.get(query_a, headers=alice).json()["certificate"]["status"] == "ausente"
        saved = client.put(query_a + "/settings", headers=alice, json=settings)
        assert saved.status_code == 200
        assert saved.json()["companies"][0]["cnpj"] == "12ABC34501DE35"
        assert client.get(emission, headers=alice).json()["settings"] is None
        assert client.get(query_b).json()["settings"] is None
        assert client.put(query_a + "/certificate", headers=alice, json=payload).status_code == 403
        result = client.put(query_a + "/certificate", headers=verified, json=payload)
        assert result.status_code == 200, result.json()
        assert result.json()["status"] == "a_vencer"
        assert SYNTHETIC_PASSWORD not in result.text
        assert "sealed_payload" not in result.json()
        assert client.get(emission, headers=alice).json()["certificate"]["status"] == "ausente"
        assert client.get(query_b).json()["certificate"]["status"] == "ausente"

        with (
            pytest.raises(DBAPIError) as denied,
            platform.database.session(platform.alice, "aal2", tenant_a) as connection,
        ):
            connection.scalar(text("SELECT sealed_payload FROM futuranext.fiscal_certificates"))
        assert denied.value.orig.sqlstate == "42501"
        with platform.database.session(platform.alice, "aal2", tenant_b) as connection:
            assert (
                connection.execute(
                    text("""
                SELECT tool_slug FROM futuranext.fiscal_certificates
            """)
                ).all()
                == []
            )

        new_payload = {
            **payload,
            "content_base64": b64encode(synthetic_certificate(ends=90)).decode(),
        }
        replacement = client.put(query_a + "/certificate", headers=verified, json=new_payload)
        assert replacement.status_code == 200, replacement.json()
        assert replacement.json()["status"] == "vigente"
        assert client.delete(query_a + "/certificate", headers=alice).status_code == 403
        assert client.delete(query_a + "/certificate", headers=verified).status_code == 204
        assert client.get(query_a, headers=verified).json()["certificate"]["status"] == "ausente"

        workbook = client.get(emission + "/template", headers=alice)
        assert workbook.status_code == 200 and workbook.content.startswith(b"PK\x03\x04")
        ccp = client.get(emission + "/template?variant=ccp", headers=alice)
        assert ccp.status_code == 200
        assert (
            ccp.headers["Content-Disposition"]
            == 'attachment; filename="Base_Emissao_Nota_Fiscal.xlsx"'
        )
        assert ccp.content == legacy_ccp_template()
        assert client.get(query_a + "/template", headers=alice).status_code == 404
        assert client.get(emission + "/template").status_code == 404
        assert client.get(emission + "/template?variant=ccp").status_code == 404
