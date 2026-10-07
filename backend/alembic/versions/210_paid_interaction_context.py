"""Bind Paid attribution to a one-use Paid interaction context.

Revision ID: 210
Revises: 209
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "210"
down_revision = "209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paid_interaction_contexts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["delivery_id"], ["paid_deliveries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="uq_paid_interaction_context_delivery"),
        sa.UniqueConstraint("token_hash", name="uq_paid_interaction_context_token_hash"),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at <= expires_at",
            name="ck_paid_interaction_context_consumption_time",
        ),
    )
    op.create_index(
        "ix_paid_interaction_context_expiry",
        "paid_interaction_contexts",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paid_interaction_context_expiry",
        table_name="paid_interaction_contexts",
    )
    op.drop_table("paid_interaction_contexts")
