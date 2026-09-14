"""Indexes for grouped creator analytics queries.

Revision ID: 009
Revises: 008
"""

from alembic import op


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_analytics_events_post_type_created",
        "analytics_events",
        ["post_id", "event_type", "created_at"],
    )
    op.create_index(
        "ix_analytics_events_target_type_created",
        "analytics_events",
        ["target_user_id", "event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analytics_events_target_type_created", table_name="analytics_events")
    op.drop_index("ix_analytics_events_post_type_created", table_name="analytics_events")