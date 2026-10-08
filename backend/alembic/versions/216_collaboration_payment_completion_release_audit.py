"""Record trusted collaboration completion and release authorization."""

import sqlalchemy as sa
from alembic import op

revision = "216"
down_revision = "215"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collaboration_payments",
        sa.Column("completion_confirmed_by_operation", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("release_authorized_by_operation", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_collaboration_payments_completion_audit",
        "collaboration_payments",
        "completion_confirmed_by_operation IS NULL OR completed_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_release_authorization_audit",
        "collaboration_payments",
        "release_authorized_by_operation IS NULL OR release_pending_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_collaboration_payments_release_authorization_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_completion_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_column(
        "collaboration_payments", "release_authorized_by_operation"
    )
    op.drop_column(
        "collaboration_payments", "completion_confirmed_by_operation"
    )
