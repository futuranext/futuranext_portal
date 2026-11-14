"""Operações offline de banco. Use somente a conexão de migrações."""

import argparse
import os
from uuid import UUID

from psycopg import Connection as PsycopgConnection
from psycopg import Error as PsycopgError
from psycopg import sql
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.database import postgres_url


def setup_role(connection: Connection, password: str) -> None:
    if len(password) < 24:
        raise ValueError("FN_APP_DB_PASSWORD precisa ter ao menos 24 caracteres.")
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('futuranext.runtime'))"))
    role = (
        connection.execute(
            text("""
        SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
               EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid) AS has_parent_role
        FROM pg_roles AS r WHERE rolname = 'fn_app'
    """)
        )
        .mappings()
        .one_or_none()
    )
    if role and (
        not role["rolcanlogin"] or any(value for key, value in role.items() if key != "rolcanlogin")
    ):
        raise ValueError("O papel fn_app existente tem configuração incompatível com runtime.")

    driver: PsycopgConnection = connection.connection.driver_connection
    verifier = driver.pgconn.encrypt_password(
        password.encode(driver.info.encoding), b"fn_app", b"scram-sha-256"
    ).decode("ascii")
    command = sql.SQL(
        "ALTER ROLE fn_app PASSWORD {}" if role else "CREATE ROLE fn_app LOGIN PASSWORD {}"
    )
    with driver.cursor() as cursor:
        cursor.execute(command.format(sql.Literal(verifier)))


def verified_profile(connection: Connection, user_id: UUID) -> None:
    email = connection.scalar(
        text("""
        SELECT lower(btrim(email)) FROM auth.users WHERE id = :user_id AND email_confirmed_at IS NOT NULL
    """),
        {"user_id": user_id},
    )
    if not email:
        raise ValueError("O UUID precisa corresponder a uma conta com e-mail confirmado no Auth.")
    connection.execute(
        text("""
        INSERT INTO futuranext.profiles (user_id, email) VALUES (:user_id, :email)
        ON CONFLICT (user_id) DO UPDATE SET email = excluded.email
        WHERE futuranext.profiles.email IS DISTINCT FROM excluded.email
    """),
        {"user_id": user_id, "email": email},
    )


def bootstrap_admin(connection: Connection, user_id: UUID) -> None:
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('futuranext.platform_roles'))"))
    current_admin = connection.scalar(
        text("""
        SELECT user_id FROM futuranext.platform_roles WHERE role = 'super_admin'
    """)
    )
    if current_admin and current_admin != user_id:
        raise ValueError("A plataforma já tem um administrador inicial.")
    verified_profile(connection, user_id)
    connection.execute(
        text("SELECT set_config('fn.actor_id', :user_id, true)"), {"user_id": str(user_id)}
    )
    connection.execute(
        text("""
        INSERT INTO futuranext.platform_roles (user_id, role) VALUES (:user_id, 'super_admin')
        ON CONFLICT (user_id) DO UPDATE SET role = excluded.role
        WHERE futuranext.platform_roles.role IS DISTINCT FROM excluded.role
    """),
        {"user_id": user_id},
    )


def assign_support(connection: Connection, user_id: UUID, tenant_id: UUID) -> None:
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('futuranext.platform_roles'))"))
    if not connection.scalar(
        text("SELECT futuranext.tenant_active(:tenant)"), {"tenant": tenant_id}
    ):
        raise ValueError("A empresa precisa existir e estar ativa.")
    current_role = connection.scalar(
        text("""
        SELECT role FROM futuranext.platform_roles WHERE user_id = :user
    """),
        {"user": user_id},
    )
    if current_role not in (None, "suporte"):
        raise ValueError("A conta já tem outro papel de plataforma.")
    verified_profile(connection, user_id)
    connection.execute(
        text("""
        INSERT INTO futuranext.platform_roles (user_id, role) VALUES (:user, 'suporte')
        ON CONFLICT (user_id) DO NOTHING
    """),
        {"user": user_id},
    )
    connection.execute(
        text("""
        INSERT INTO futuranext.support_assignments (tenant_id, user_id) VALUES (:tenant, :user)
        ON CONFLICT (tenant_id, user_id) DO UPDATE SET active = true
        WHERE NOT futuranext.support_assignments.active
    """),
        {"tenant": tenant_id, "user": user_id},
    )


def revoke_support(connection: Connection, user_id: UUID, tenant_id: UUID) -> None:
    connection.execute(
        text("""
        UPDATE futuranext.support_assignments SET active = false
        WHERE tenant_id = :tenant AND user_id = :user AND active
    """),
        {"tenant": tenant_id, "user": user_id},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-role")
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("user_id", type=UUID)
    for name in ("assign-support", "revoke-support"):
        support = commands.add_parser(name)
        support.add_argument("user_id", type=UUID)
        support.add_argument("tenant_id", type=UUID)
    args = parser.parse_args()
    try:
        engine = create_engine(
            postgres_url(os.environ["FN_MIGRATION_DATABASE_URL"]), hide_parameters=True
        )
        try:
            with engine.begin() as connection:
                if args.command == "setup-role":
                    setup_role(connection, os.environ["FN_APP_DB_PASSWORD"])
                elif args.command == "bootstrap-admin":
                    bootstrap_admin(connection, args.user_id)
                elif args.command == "assign-support":
                    assign_support(connection, args.user_id, args.tenant_id)
                else:
                    revoke_support(connection, args.user_id, args.tenant_id)
        finally:
            engine.dispose()
    except (KeyError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    except (PsycopgError, SQLAlchemyError):
        parser.exit(1, "Falha de banco. Verifique conexão, permissões e estado da migração.\n")


if __name__ == "__main__":
    main()
