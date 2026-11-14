"""Limita tentativas de envio e preserva o estado de convites aceitos."""

from alembic import op

revision = "0002_invitation_delivery"
down_revision = "0001_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE futuranext.invitations ADD COLUMN last_send_attempt_at timestamptz")
    op.execute("GRANT UPDATE (last_send_attempt_at) ON futuranext.invitations TO fn_app")
    op.execute("ALTER TABLE futuranext.invitations DROP CONSTRAINT invitations_accepted_by_fkey")
    op.execute("ALTER TABLE futuranext.invitations DROP CONSTRAINT invitations_check")
    op.execute("""
        ALTER TABLE futuranext.invitations ADD CONSTRAINT invitations_accepted_by_fkey
            FOREIGN KEY (accepted_by) REFERENCES futuranext.profiles(user_id) ON DELETE SET NULL,
        ADD CONSTRAINT invitations_check CHECK (accepted_at IS NOT NULL OR accepted_by IS NULL)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE futuranext.invitations DROP COLUMN last_send_attempt_at")
    op.execute("ALTER TABLE futuranext.invitations DROP CONSTRAINT invitations_accepted_by_fkey")
    op.execute("ALTER TABLE futuranext.invitations DROP CONSTRAINT invitations_check")
    op.execute("""
        ALTER TABLE futuranext.invitations ADD CONSTRAINT invitations_accepted_by_fkey
            FOREIGN KEY (accepted_by) REFERENCES futuranext.profiles(user_id),
        ADD CONSTRAINT invitations_check CHECK ((accepted_at IS NULL) = (accepted_by IS NULL))
    """)
