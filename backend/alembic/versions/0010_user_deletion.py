"""Exclusão definitiva de contas do portal e do histórico pessoal."""

from pathlib import Path

from alembic import op

revision = "0010_user_deletion"
down_revision = "0009_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER futuranext_clean_deleted_user ON auth.users;
        DROP FUNCTION futuranext.clean_deleted_user();
        DROP FUNCTION futuranext.portal_user_deletion_email(uuid);
        ALTER TABLE futuranext.audit_logs DROP CONSTRAINT audit_logs_actor_fkey;
        DROP INDEX futuranext.audit_logs_by_actor;
        CREATE OR REPLACE FUNCTION futuranext.search_accounts(search_term text)
        RETURNS TABLE (user_id uuid, email text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            SELECT account.id, account.email::text FROM auth.users account
            WHERE futuranext.tenant() IS NULL AND futuranext.has_permission('admin.tenant.manage')
              AND account.email_confirmed_at IS NOT NULL AND account.email IS NOT NULL
              AND position(lower(btrim(search_term)) in lower(account.email)) > 0
            ORDER BY lower(account.email), account.id LIMIT 50
        $$;
    """)
