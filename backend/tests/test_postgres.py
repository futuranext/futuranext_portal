import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.database import Database, postgres_url
from app.db_admin import assign_support, bootstrap_admin, revoke_support, setup_role
from app.repository import Repository


@dataclass(frozen=True)
class Platform:
    owner: Engine
    database: Database
    admin: UUID
    alice: UUID
    bob: UUID
    support: UUID
    tenant_a: UUID
    tenant_b: UUID
    product_a: UUID
    product_b: UUID
    suffix: str


def add_auth_user(owner: Connection, email: str, *, confirmed: bool = True) -> UUID:
    subject = uuid4()
    owner.execute(
        text("""
            INSERT INTO auth.users (id, email, email_confirmed_at)
            VALUES (:subject, :email, :confirmed_at)
        """),
        {
            "subject": subject,
            "email": email,
            "confirmed_at": datetime.now(UTC) if confirmed else None,
        },
    )
    return subject


def email_for(name: str, suffix: str) -> str:
    return f"{name}-{suffix}@example.test"


def confirm_membership(
    database: Database,
    admin: UUID,
    user: UUID,
    tenant: UUID,
    email: str,
    role: str = "cliente_usuario",
) -> UUID:
    with database.session(admin, "aal2", tenant) as connection:
        invite = Repository(connection).create_invitation(tenant, email, role)
        assert invite is not None
    with database.session(user, "aal1") as connection:
        assert Repository(connection).accept_invitation(invite["id"], email)
    return invite["id"]


@pytest.fixture(scope="module")
def platform() -> Iterator[Platform]:
    migration_url = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    app_url = os.environ.get("TEST_DATABASE_URL")
    if not migration_url or not app_url:
        pytest.skip(
            "Defina TEST_MIGRATION_DATABASE_URL e TEST_DATABASE_URL para o PostgreSQL descartável."
        )

    owner_dsn, app_dsn = postgres_url(migration_url), postgres_url(app_url)
    assert owner_dsn.host in {"localhost", "127.0.0.1", "::1", "postgres"}
    assert owner_dsn.host == app_dsn.host and owner_dsn.port == app_dsn.port
    assert owner_dsn.database == app_dsn.database and "test" in (owner_dsn.database or "")
    assert app_dsn.username == "fn_app" and app_dsn.password is not None
    owner = create_engine(owner_dsn, hide_parameters=True, connect_args={"connect_timeout": 3})
    runtime = create_engine(app_dsn, hide_parameters=True, pool_size=1, max_overflow=1)
    database = Database(runtime)
    suffix = uuid4().hex[:12]
    try:
        with owner.begin() as connection:
            # Somente banco local de teste. O Supabase real fornece auth.users.
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
            connection.execute(
                text("""
                CREATE TABLE IF NOT EXISTS auth.users (
                    id uuid PRIMARY KEY, email text NOT NULL UNIQUE, email_confirmed_at timestamptz,
                    encrypted_password text, invited_at timestamptz, last_sign_in_at timestamptz,
                    raw_app_meta_data jsonb
                )
            """)
            )
            setup_role(connection, app_dsn.password)
        with pytest.MonkeyPatch.context() as env:
            env.setenv("FN_MIGRATION_DATABASE_URL", migration_url)
            command.upgrade(
                Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
            )

        with owner.begin() as connection:
            admin = connection.scalar(
                text("""
                SELECT user_id FROM futuranext.platform_roles WHERE role = 'super_admin'
            """)
            )
            if admin is None:
                admin = add_auth_user(connection, email_for("admin", suffix))
            bootstrap_admin(connection, admin)
            bootstrap_admin(connection, admin)
            alice = add_auth_user(connection, email_for("alice", suffix))
            bob = add_auth_user(connection, email_for("bob", suffix))
            support = add_auth_user(connection, email_for("suporte", suffix))

        with database.session(admin, "aal2") as connection:
            repo = Repository(connection)
            tenant_a = repo.create_organization("Empresa A", f"a-{suffix}")
            tenant_b = repo.create_organization("Empresa B", f"b-{suffix}")
            products = {item["slug"]: item["id"] for item in repo.list_products()}
            assert tenant_a and tenant_b and {"barbearia", "clinica", "nfse"} <= products.keys()

        with owner.begin() as connection:
            assign_support(connection, support, tenant_a["id"])

        for tenant, subject, name, product in (
            (tenant_a["id"], alice, "alice", products["barbearia"]),
            (tenant_b["id"], bob, "bob", products["clinica"]),
        ):
            confirm_membership(
                database,
                admin,
                subject,
                tenant,
                email_for(name, suffix),
                "cliente_admin" if name == "alice" else "cliente_usuario",
            )
            with database.session(admin, "aal2", tenant) as connection:
                assert Repository(connection).upsert_entitlement(tenant, product, True, None, None)

        yield Platform(
            owner,
            database,
            admin,
            alice,
            bob,
            support,
            tenant_a["id"],
            tenant_b["id"],
            products["barbearia"],
            products["clinica"],
            suffix,
        )
    finally:
        owner.dispose()
        runtime.dispose()


def test_rls_and_readiness_deny_unscoped_access(platform: Platform) -> None:
    assert platform.database.ready()
    assert not Database(platform.owner).ready()
    with platform.database.engine.begin() as connection:
        assert connection.scalar(text("SELECT current_user = 'fn_app'"))
        assert connection.scalar(text("SELECT row_security_active('futuranext.organizations')"))
        assert Repository(connection).list_organizations() == []
        assert Repository(connection).list_products() == []


def test_admin_grants_and_revokes_individual_company_access(platform: Platform) -> None:
    bob_email = email_for("bob", platform.suffix)
    with platform.owner.begin() as connection:
        unconfirmed = add_auth_user(
            connection, email_for("pending-access", platform.suffix), confirmed=False
        )
    with platform.database.session(platform.admin, "aal1") as connection:
        repo = Repository(connection)
        assert repo.search_accounts(bob_email) == []
        assert not repo.set_account_access(platform.bob, platform.tenant_a, "cliente_admin", True)
    with platform.database.session(platform.admin, "aal2") as connection:
        repo = Repository(connection)
        assert repo.search_accounts(bob_email) == [{"user_id": platform.bob, "email": bob_email}]
        assert not repo.set_account_access(unconfirmed, platform.tenant_a, "cliente_admin", True)
        assert repo.set_account_access(platform.bob, platform.tenant_a, "cliente_admin", True)
        access = {record["tenant_id"]: record for record in repo.account_access(platform.bob)}
        assert access[platform.tenant_a]["role"] == "cliente_admin"
        assert access[platform.tenant_a]["granted"]
        assert access[platform.tenant_b]["granted"]
    with platform.database.session(platform.bob, "aal1", platform.tenant_a) as connection:
        assert Repository(connection).can_use_tool(platform.tenant_a, "barbearia", write=True)
        assert not Repository(connection).can_use_tool(platform.tenant_b, "clinica")
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        invite = Repository(connection).create_invitation(
            platform.tenant_a, bob_email, "cliente_admin"
        )
        assert invite is not None
    with platform.database.session(platform.admin, "aal2") as connection:
        assert Repository(connection).set_account_access(
            platform.bob, platform.tenant_a, "cliente_admin", False
        )
    with platform.database.session(platform.bob, "aal1", platform.tenant_a) as connection:
        assert not Repository(connection).can_use_tool(platform.tenant_a, "barbearia")
    with platform.database.session(platform.bob, "aal1") as connection:
        repo = Repository(connection)
        assert repo.pending_invitations(bob_email) == []
        assert not repo.accept_invitation(invite["id"], bob_email)


def test_password_generation_revokes_preexisting_portal_sessions(platform: Platform) -> None:
    with platform.owner.begin() as connection:
        subject = add_auth_user(connection, email_for("password-generation", platform.suffix))
    old_version, new_version = str(uuid4()), str(uuid4())
    for version, pending in ((old_version, True), (new_version, False)):
        with platform.owner.begin() as connection:
            connection.execute(text("""
                UPDATE auth.users SET raw_app_meta_data = jsonb_build_object(
                    'futuranext_first_password', CAST(:pending AS boolean),
                    'futuranext_password_version', CAST(:version AS text)
                ) WHERE id = :subject
            """), {"pending": pending, "version": version, "subject": subject})
        with platform.database.session(subject, "aal2") as connection:
            repo = Repository(connection)
            assert not repo.password_session_current(None)
            assert not repo.password_session_current(old_version)
            assert repo.password_session_current(new_version) == (not pending)


def test_seeded_tools_and_whatsapp_offer_activation(platform: Platform) -> None:
    with platform.database.session(platform.admin, "aal2") as connection:
        repo = Repository(connection)
        companies = {company["name"]: company["id"] for company in repo.list_organizations()}
        assert {"Equinos Seguros", "CCP - Clegio Clemente Pavan", "PetCamp", "Estetica Paraiso",
                "Barbearia Stylus"} <= companies.keys()
        company = companies["Barbearia Stylus"]
        assert repo.set_account_access(platform.alice, company, "cliente_admin", True)
        products = {product["slug"]: product["id"] for product in repo.list_products()}
        whatsapp = products["whatsapp"]
    with platform.database.session(platform.alice, "aal2", company) as connection:
        repo = Repository(connection)
        assert repo.can_use_tool(company, "barbearia", write=True)
        assert repo.tool_offers(platform.tenant_b) == []
        assert repo.request_tool(platform.tenant_b, whatsapp) is None
        assert repo.request_tool(company, products["cotacao-seguros"]) is None
        request_id = repo.request_tool(company, whatsapp)
        assert request_id is not None and repo.request_tool(company, whatsapp) == request_id
        assert not repo.review_tool_request(request_id, "approved")
    with platform.database.session(platform.admin, "aal2") as connection:
        repo = Repository(connection)
        assert any(request["id"] == request_id for request in repo.list_tool_requests())
        assert repo.review_tool_request(request_id, "approved")
        assert repo.review_tool_request(request_id, "approved")
        assert not repo.review_tool_request(request_id, "rejected")
    with platform.database.session(platform.alice, "aal1", company) as connection:
        repo = Repository(connection)
        assert repo.can_use_tool(company, "whatsapp")
        assert all(offer["id"] != whatsapp for offer in repo.tool_offers(company))
    with platform.database.session(platform.bob, "aal1", platform.tenant_b) as connection:
        assert not Repository(connection).can_use_tool(platform.tenant_b, "whatsapp")
    with platform.database.session(platform.admin, "aal2") as connection:
        assert Repository(connection).set_account_access(platform.alice, company, "cliente_admin", False)
    with platform.database.session(platform.admin, "aal2", company) as connection:
        assert Repository(connection).upsert_entitlement(company, whatsapp, False, None, None)


def test_isolation_on_organization_members_and_products(platform: Platform) -> None:
    with platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection:
        repo = Repository(connection)
        assert repo.get_organization(platform.tenant_b) is None
        assert repo.list_memberships(platform.tenant_b) == []
        assert repo.tenant_products(platform.tenant_b) == []
        assert repo.update_organization(platform.tenant_b, "Inacessível", True) is None
        assert {row["id"] for row in repo.tenant_products(platform.tenant_a)} == {
            platform.product_a
        }
    with platform.database.session(platform.alice, "aal2", platform.tenant_b) as connection:
        repo = Repository(connection)
        assert not repo.has_permission("tenant.access")
        assert repo.get_organization(platform.tenant_b) is None
        assert repo.tenant_products(platform.tenant_b) == []
        assert repo.list_memberships(platform.tenant_b) == []
    with (
        pytest.raises(DBAPIError) as failure,
        platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection,
    ):
        Repository(connection).upsert_entitlement(
            platform.tenant_b, platform.product_b, False, None, None
        )
    assert failure.value.orig.sqlstate == "42501"


def test_multi_company_switch_still_enforces_selected_tenant(platform: Platform) -> None:
    with platform.owner.begin() as owner:
        email = email_for("multi", platform.suffix)
        subject = add_auth_user(owner, email)
    for tenant in (platform.tenant_a, platform.tenant_b):
        confirm_membership(platform.database, platform.admin, subject, tenant, email)
    with platform.database.session(subject, "aal1") as connection:
        assert {item["id"] for item in Repository(connection).context()["organizations"]} == {
            platform.tenant_a,
            platform.tenant_b,
        }
        assert Repository(connection).list_products() == []
    with platform.database.session(subject, "aal1", platform.tenant_b) as connection:
        repo = Repository(connection)
        assert {item["id"] for item in repo.tenant_products(platform.tenant_b)} == {
            platform.product_b
        }
        assert repo.tenant_products(platform.tenant_a) == []
        assert repo.get_organization(platform.tenant_a) is None


def test_scoped_support_and_admin_require_mfa(platform: Platform) -> None:
    for actor in (platform.admin, platform.support):
        with platform.database.session(actor, "aal1") as connection:
            repo = Repository(connection)
            assert not repo.has_permission("admin.access")
            assert repo.list_organizations() == []
            assert repo.list_audit() == []
    with platform.database.session(platform.support, "aal2") as connection:
        repo = Repository(connection)
        assert repo.has_permission("admin.access")
        assert not repo.has_permission("admin.tenant.manage")
        assert {item["id"] for item in repo.list_organizations()} == {platform.tenant_a}
        assert repo.list_audit() == []
    with platform.database.session(platform.support, "aal2", platform.tenant_b) as connection:
        assert Repository(connection).list_memberships(platform.tenant_b) == []
    with platform.database.session(platform.support, "aal2", platform.tenant_a) as connection:
        assert any(
            row["user_id"] == platform.alice
            for row in Repository(connection).list_memberships(platform.tenant_a)
        )
    with platform.owner.begin() as owner:
        revoke_support(owner, platform.support, platform.tenant_a)
    with platform.database.session(platform.support, "aal2", platform.tenant_a) as connection:
        repo = Repository(connection)
        assert not repo.has_permission("admin.users.read")
        assert repo.list_memberships(platform.tenant_a) == []


def test_product_requires_current_license_and_individual_permission(platform: Platform) -> None:
    now = datetime.now(UTC)
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        Repository(connection).upsert_entitlement(
            platform.tenant_a,
            platform.product_b,
            True,
            now - timedelta(days=5),
            now - timedelta(days=1),
        )
    with platform.owner.begin() as owner:
        owner.execute(
            text("""
            INSERT INTO futuranext.user_permissions (tenant_id, user_id, product_id, permission, allowed)
            VALUES (:tenant, :user, :product, 'product.view', false)
        """),
            {"tenant": platform.tenant_a, "user": platform.alice, "product": platform.product_a},
        )
    try:
        with platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection:
            repo = Repository(connection)
            assert repo.has_permission("product.view")
            assert repo.tenant_products(platform.tenant_a) == []
            assert repo.list_products() == []
    finally:
        with platform.owner.begin() as owner:
            owner.execute(
                text("""
                DELETE FROM futuranext.user_permissions WHERE tenant_id = :tenant AND user_id = :user
            """),
                {"tenant": platform.tenant_a, "user": platform.alice},
            )


def test_invite_delivery_and_acceptance_use_confirmed_identity(platform: Platform) -> None:
    email = email_for("convite", platform.suffix)
    with platform.owner.begin() as owner:
        invited = add_auth_user(owner, email, confirmed=False)
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        repo = Repository(connection)
        invite = repo.create_invitation(platform.tenant_a, email.upper(), "cliente_usuario")
        assert invite is not None
        assert repo.create_invitation(platform.tenant_a, email, "cliente_usuario") == invite
        assert repo.claim_invitation_delivery(invite["id"])
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        repo = Repository(connection)
        assert not repo.claim_invitation_delivery(invite["id"])
        assert repo.record_invitation_delivery(invite["id"], "sent")["delivery"] == "sent"
        assert not repo.claim_invitation_delivery(invite["id"])

    with platform.owner.begin() as owner:
        owner.execute(
            text("""
            UPDATE futuranext.invitations SET last_send_attempt_at = now() - interval '61 seconds'
            WHERE id = :invite
        """),
            {"invite": invite["id"]},
        )
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        repo = Repository(connection)
        assert repo.claim_invitation_delivery(invite["id"])
        assert repo.record_invitation_delivery(invite["id"], "sent")["delivery"] == "sent"

    with platform.database.session(invited, "aal1") as connection:
        repo = Repository(connection)
        assert repo.pending_invitations(email) == []
        assert not repo.accept_invitation(invite["id"], email)
    with platform.database.session(platform.bob, "aal1") as connection:
        assert not Repository(connection).accept_invitation(invite["id"], email)

    with platform.owner.begin() as owner:
        owner.execute(
            text("UPDATE auth.users SET email_confirmed_at = now() WHERE id = :user"),
            {"user": invited},
        )
    with platform.database.session(invited, "aal1") as connection:
        repo = Repository(connection)
        assert [row["id"] for row in repo.pending_invitations(email)] == [invite["id"]]
        assert repo.accept_invitation(invite["id"], email)
        assert repo.accept_invitation(invite["id"], email)
        assert repo.pending_invitations(email) == []
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        assert any(
            row["email"] == email and row["role"] == "cliente_usuario"
            for row in Repository(connection).list_memberships(platform.tenant_a)
        )
    with platform.owner.begin() as owner:
        owner.execute(text("DELETE FROM auth.users WHERE id = :user"), {"user": invited})
        assert not owner.scalar(
            text("SELECT EXISTS (SELECT FROM futuranext.invitations WHERE id = :invite)"),
            {"invite": invite["id"]},
        )


def test_customer_admin_cannot_promote_member(platform: Platform) -> None:
    email = email_for("regular", platform.suffix)
    with platform.owner.begin() as owner:
        user = add_auth_user(owner, email)
    confirm_membership(platform.database, platform.admin, user, platform.tenant_a, email)
    with platform.database.session(platform.alice, "aal1", platform.tenant_a) as connection:
        assert not Repository(connection).has_permission("admin.users.manage")
    with (
        pytest.raises(DBAPIError) as failure,
        platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection,
    ):
        repo = Repository(connection)
        assert repo.has_permission("admin.users.manage")
        repo.update_membership(platform.tenant_a, user, "cliente_admin", True)
    assert failure.value.orig.sqlstate == "42501"
    with platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection:
        member = Repository(connection).update_membership(
            platform.tenant_a, user, "cliente_usuario", False
        )
        assert member and not member["active"]


def test_audit_records_actor_without_payload(platform: Platform) -> None:
    with platform.database.session(platform.admin, "aal2") as connection:
        events = Repository(connection).list_audit()
        assert any(
            event["resource"] == "organizations"
            and event["tenant_id"] == platform.tenant_a
            and event["actor_id"] == platform.admin
            and event["operation"] == "INSERT"
            for event in events
        )
    with (
        pytest.raises(DBAPIError) as failure,
        platform.database.session(platform.admin, "aal2") as connection,
    ):
        connection.execute(text("UPDATE futuranext.audit_logs SET resource = 'alterado'"))
    assert failure.value.orig.sqlstate == "42501"


def test_pool_clears_actor_after_commit_and_rollback(platform: Platform) -> None:
    with platform.database.session(platform.admin, "aal2", platform.tenant_a) as connection:
        process_id = connection.scalar(text("SELECT pg_backend_pid()"))
        assert Repository(connection).has_permission("admin.access")
    with (
        pytest.raises(RuntimeError, match="rollback"),
        platform.database.session(platform.alice, "aal2", platform.tenant_a) as connection,
    ):
        assert connection.scalar(text("SELECT pg_backend_pid()")) == process_id
        assert Repository(connection).has_permission("tenant.access")
        raise RuntimeError("rollback")
    with platform.database.engine.begin() as connection:
        assert connection.scalar(text("SELECT pg_backend_pid()")) == process_id
        assert connection.scalar(
            text("SELECT futuranext.actor() IS NULL AND futuranext.tenant() IS NULL")
        )
        assert not Repository(connection).has_permission("admin.access")
        assert Repository(connection).list_organizations() == []
