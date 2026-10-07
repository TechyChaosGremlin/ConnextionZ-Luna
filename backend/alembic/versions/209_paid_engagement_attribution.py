"""Add authoritative Paid provenance to analytics and post interactions.

Revision ID: 209
Revises: 208
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "209"
down_revision = "208"
branch_labels = None
depends_on = None


_ATTRIBUTION_TABLES = (
    "interaction_signals",
    "analytics_events",
    "post_likes",
    "post_saves",
    "post_shares",
    "post_watches",
)
_INDEXES = {
    "interaction_signals": (
        "ix_interaction_signals_paid_campaign_created",
        ["paid_campaign_id", "created_at"],
    ),
    "analytics_events": (
        "ix_analytics_events_paid_campaign_created",
        ["paid_campaign_id", "created_at"],
    ),
    "post_likes": ("ix_post_likes_paid_post", ["paid_delivery_id", "post_id"]),
    "post_saves": ("ix_post_saves_paid_post", ["paid_delivery_id", "post_id"]),
    "post_shares": ("ix_post_shares_paid_post", ["paid_delivery_id", "post_id"]),
    "post_watches": ("ix_post_watches_paid_post", ["paid_delivery_id", "post_id"]),
}


def upgrade() -> None:
    for table in _ATTRIBUTION_TABLES:
        op.add_column(
            table,
            sa.Column("paid_delivery_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("paid_campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_check_constraint(
            f"ck_{table}_paid_attribution",
            table,
            "(paid_delivery_id IS NULL) = (paid_campaign_id IS NULL)",
        )
        index_name, columns = _INDEXES[table]
        op.create_index(index_name, table, columns)


def downgrade() -> None:
    for table in reversed(_ATTRIBUTION_TABLES):
        index_name, _ = _INDEXES[table]
        op.drop_index(index_name, table_name=table)
        op.drop_constraint(f"ck_{table}_paid_attribution", table, type_="check")
        op.drop_column(table, "paid_campaign_id")
        op.drop_column(table, "paid_delivery_id")
