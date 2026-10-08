"""Record idempotent internal hold authorization and reversal audit.

Revision ID: 215
Revises: 214
"""

import sqlalchemy as sa
from alembic import op

revision = "215"
down_revision = "214"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collaboration_payments",
        sa.Column("hold_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("authorization_reference", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("authorized_by_operation", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("hold_reversed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "collaboration_payments",
        sa.Column("hold_reversed_by_operation", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_collaboration_payments_hold_idempotency_key",
        "collaboration_payments",
        ["hold_idempotency_key"],
    )
    op.create_check_constraint(
        "ck_collaboration_payments_hold_authorization_audit",
        "collaboration_payments",
        "(hold_idempotency_key IS NULL AND authorized_at IS NULL "
        "AND authorized_by_operation IS NULL) OR "
        "(hold_idempotency_key IS NOT NULL AND authorized_at IS NOT NULL "
        "AND authorized_by_operation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_held_states_authorized",
        "collaboration_payments",
        "state NOT IN ('AUTHORIZED_HELD', 'COLLABORATION_ACTIVE', 'COMPLETED', "
        "'RELEASE_PENDING', 'RELEASED', 'REFUNDED', 'DISPUTED') OR "
        "(hold_idempotency_key IS NOT NULL AND authorized_at IS NOT NULL "
        "AND authorized_by_operation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_collaboration_payments_hold_reversal_audit",
        "collaboration_payments",
        "(hold_reversed_at IS NULL AND hold_reversed_by_operation IS NULL) OR "
        "(hold_reversed_at IS NOT NULL AND hold_reversed_by_operation IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_collaboration_payments_hold_reversal_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_held_states_authorized",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_payments_hold_authorization_audit",
        "collaboration_payments",
        type_="check",
    )
    op.drop_constraint(
        "uq_collaboration_payments_hold_idempotency_key",
        "collaboration_payments",
        type_="unique",
    )
    op.drop_column("collaboration_payments", "hold_reversed_by_operation")
    op.drop_column("collaboration_payments", "hold_reversed_at")
    op.drop_column("collaboration_payments", "authorized_by_operation")
    op.drop_column("collaboration_payments", "authorization_reference")
    op.drop_column("collaboration_payments", "hold_idempotency_key")
