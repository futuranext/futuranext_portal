"""Núcleo da plataforma, papéis, auditoria e isolamento."""

from pathlib import Path

from alembic import op

revision = "0001_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).with_name("0001_platform.sql")
    op.execute(source.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP SCHEMA futuranext CASCADE")
    op.execute("REVOKE SELECT ON futuranext_meta.alembic_version FROM fn_app")
    op.execute("REVOKE USAGE ON SCHEMA futuranext_meta FROM fn_app")
