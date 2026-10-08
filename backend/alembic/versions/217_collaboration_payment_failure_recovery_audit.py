"""Add audit data for provider-neutral financial recovery paths."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "217"
down_revision = "216"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collaboration_payments",
        sa.Column("cancelled_by_operation", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("refund_authorized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column(
            "refund_authorized_by_operation", sa.String(length=64), nullable=True
        ),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("disputed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("disputed_by_operation", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("dispute_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column(
            "dispute_resolved_by_operation", sa.String(length=64), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_collaboration_payments_disputed_by_user_id_users",
        "collaboration_payments",
        "users",
        ["disputed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE collaboration_payments "
        "SET refunded_at = COALESCE(refunded_at, updated_at), "
        "refund_authorized_at = COALESCE(refunded_at, updated_at), "
        "refund_authorized_by_operation = 'legacy_internal_refund_state' "
        "WHERE state = 'REFUNDED'"
    )
    op.execute(
        "UPDATE collaboration_payments "
        "SET disputed_at = COALESCE(disputed_at, updated_at), "
        "disputed_by_operation = 'legacy_internal_dispute_state' "
        "WHERE state = 'DISPUTED'"
    )
    op.execute(
        "UPDATE collaboration_payments "
        "SET cancelled_at = COALESCE(cancelled_at, updated_at), "
        "cancelled_by_operation = 'legacy_internal_cancellation' "
        "WHERE state = 'CANCELLED'"
    )
    op.create_check_constraint(
        "ck_collaboration_payments_refund_authorization_audit",
        "collaboration_payments",
        "(refund_authorized_at IS NULL AND "
        "refund_authorized_by_operation IS NULL) OR "
        "(refund_authorized_at IS NOT NULL AND "
        "refund_authorized_by_operation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_refunded_has_authorization",
        "collaboration_payments",
        "state <> 'REFUNDED' OR "
        "(refund_authorized_at IS NOT NULL AND "
        "refund_authorized_by_operation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_dispute_initiation_audit",
        "collaboration_payments",
        "state <> 'DISPUTED' OR "
        "(disputed_at IS NOT NULL AND disputed_by_operation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_dispute_resolution_audit",
        "collaboration_payments",
        "(dispute_resolved_at IS NULL AND "
        "dispute_resolved_by_operation IS NULL) OR "
        "(dispute_resolved_at IS NOT NULL AND "
        "dispute_resolved_by_operation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_cancellation_audit",
        "collaboration_payments",
        "cancelled_by_operation IS NULL OR cancelled_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_collaboration_payments_cancellation_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_dispute_resolution_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_dispute_initiation_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_refunded_has_authorization",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_refund_authorization_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "fk_collaboration_payments_disputed_by_user_id_users",
        "collaboration_payments",
        type_="foreignkey",
    )
    op.drop_column("collaboration_payments", "dispute_resolved_by_operation")
    op.drop_column("collaboration_payments", "dispute_resolved_at")
    op.drop_column("collaboration_payments", "disputed_by_operation")
    op.drop_column("collaboration_payments", "disputed_by_user_id")
    op.drop_column("collaboration_payments", "refund_authorized_by_operation")
    op.drop_column("collaboration_payments", "refund_authorized_at")
    op.drop_column("collaboration_payments", "cancelled_by_operation")
