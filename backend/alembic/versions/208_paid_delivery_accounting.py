"""Persist server-selected Paid deliveries and authoritative impression history.

Revision ID: 208
Revises: 207
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "208"
down_revision = "207"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paid_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("impressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["paid_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "impressed_at IS NULL OR impressed_at >= selected_at", name="ck_paid_deliveries_time"
        ),
    )
    op.create_index(
        "ix_paid_deliveries_campaign_viewer_time",
        "paid_deliveries",
        ["campaign_id", "viewer_id", "impressed_at"],
    )
    op.create_index("ix_paid_deliveries_post", "paid_deliveries", ["post_id"])
    op.create_index("ix_paid_deliveries_viewer", "paid_deliveries", ["viewer_id"])


def downgrade() -> None:
    op.drop_index("ix_paid_deliveries_viewer", table_name="paid_deliveries")
    op.drop_index("ix_paid_deliveries_post", table_name="paid_deliveries")
    op.drop_index("ix_paid_deliveries_campaign_viewer_time", table_name="paid_deliveries")
    op.drop_table("paid_deliveries")
