"""Add the separate Paid algorithm's campaign foundation.

Revision ID: 207
Revises: 206
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "207"
down_revision = "206"
branch_labels = None
depends_on = None

STATUSES = ("draft", "scheduled", "active", "paused", "exhausted", "completed", "cancelled")


def upgrade() -> None:
    status = postgresql.ENUM(*STATUSES, name="paid_campaign_status", create_type=False)
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "paid_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", status, server_default="draft", nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("budget_minor_units", sa.Integer(), nullable=False),
        sa.Column("spent_minor_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("impressions_delivered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reach_delivered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_impressions", sa.Integer(), nullable=True),
        sa.Column("max_reach", sa.Integer(), nullable=True),
        sa.Column("frequency_cap", sa.Integer(), nullable=True),
        sa.Column("frequency_window_seconds", sa.Integer(), nullable=True),
        sa.Column("targeting", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("end_at > start_at", name="ck_paid_campaigns_window"),
        sa.CheckConstraint("budget_minor_units > 0", name="ck_paid_campaigns_budget"),
        sa.CheckConstraint(
            "spent_minor_units >= 0 AND spent_minor_units <= budget_minor_units",
            name="ck_paid_campaigns_spend",
        ),
        sa.CheckConstraint(
            "impressions_delivered >= 0 AND reach_delivered >= 0 "
            "AND reach_delivered <= impressions_delivered",
            name="ck_paid_campaigns_delivery",
        ),
        sa.CheckConstraint(
            "max_impressions IS NULL OR "
            "(max_impressions > 0 AND impressions_delivered <= max_impressions)",
            name="ck_paid_campaigns_impressions",
        ),
        sa.CheckConstraint(
            "max_reach IS NULL OR (max_reach > 0 AND reach_delivered <= max_reach)",
            name="ck_paid_campaigns_reach",
        ),
        sa.CheckConstraint(
            "(frequency_cap IS NULL AND frequency_window_seconds IS NULL) OR "
            "(frequency_cap IS NOT NULL AND frequency_window_seconds IS NOT NULL "
            "AND frequency_cap > 0 AND frequency_window_seconds > 0)",
            name="ck_paid_campaigns_frequency",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_paid_campaigns_currency",
        ),
    )
    op.create_index("ix_paid_campaigns_owner_created", "paid_campaigns", ["owner_id", "created_at"])
    op.create_index("ix_paid_campaigns_post", "paid_campaigns", ["post_id"])
    op.create_index(
        "ix_paid_campaigns_status_window", "paid_campaigns", ["status", "start_at", "end_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_paid_campaigns_status_window", table_name="paid_campaigns")
    op.drop_index("ix_paid_campaigns_post", table_name="paid_campaigns")
    op.drop_index("ix_paid_campaigns_owner_created", table_name="paid_campaigns")
    op.drop_table("paid_campaigns")
    postgresql.ENUM(*STATUSES, name="paid_campaign_status").drop(op.get_bind(), checkfirst=True)
