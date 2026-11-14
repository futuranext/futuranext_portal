import os

from sqlalchemy import create_engine, pool, text

from alembic import context
from app.database import postgres_url


def migrate() -> None:
    url = postgres_url(os.environ["FN_MIGRATION_DATABASE_URL"])
    options = {"version_table_schema": "futuranext_meta"}
    if context.is_offline_mode():
        context.configure(url=url, literal_binds=True, **options)
        with context.begin_transaction():
            context.execute("CREATE SCHEMA IF NOT EXISTS futuranext_meta")
            context.run_migrations()
        return

    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS futuranext_meta"))
            context.configure(connection=connection, **options)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


migrate()
