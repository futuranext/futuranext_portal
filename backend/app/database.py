from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError


def postgres_url(value: str) -> URL:
    url = make_url(value)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise ValueError("É necessária uma conexão PostgreSQL via psycopg.")
    return url.set(drivername="postgresql+psycopg")


class Database:
    def __init__(self, engine: Engine | str):
        self.engine = (
            create_engine(
                postgres_url(engine),
                hide_parameters=True,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 3},
            )
            if isinstance(engine, str)
            else engine
        )

    @contextmanager
    def session(
        self, actor_id: UUID, aal: str, tenant_id: UUID | None = None
    ) -> Iterator[Connection]:
        with self.engine.begin() as connection:
            connection.execute(
                text("""
                    SELECT set_config('fn.actor_id', :actor, true),
                           set_config('fn.tenant_id', :tenant, true),
                           set_config('fn.aal', :aal, true),
                           set_config('statement_timeout', '5000', true)
                """),
                {"actor": str(actor_id), "tenant": str(tenant_id) if tenant_id else "", "aal": aal},
            )
            yield connection

    def ready(self) -> bool:
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                return bool(
                    connection.scalar(
                        text("""
                    SELECT current_user = 'fn_app'
                       AND row_security_active('futuranext.organizations')
                       AND version_num = '0012_whatsapp_consent'
                    FROM futuranext_meta.alembic_version
                """)
                    )
                )
        except SQLAlchemyError:
            return False
