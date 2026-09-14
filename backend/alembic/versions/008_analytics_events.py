"""General-purpose analytics event log (analytics_events) — Event Tracking v1.

Revision ID: 008
Revises: 007
Create Date: 2026-09-06 00:00:00.000000
"""

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


EVENT_TYPES = (
    "video_impression",
    "video_viewed",
    "video_completed",
    "video_skipped",
    "video_watched",
    "like_created",
    "like_removed",
    "comment_created",
    "share_created",
    "save_created",
    "follow_created",
    "follow_removed",
    "profile_viewed",
    "video_uploaded",
    "video_published",
    "sound_used",
    "search_performed",
    "collab_created",
    "notification_opened",
)


def upgrade() -> None:
    event_type_enum = postgresql.ENUM(*EVENT_TYPES, name="analytics_event_type", create_type=False)
    event_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "analytics_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", event_type_enum, nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_analytics_events_user_id", "analytics_events", ["user_id"])
    op.create_index("ix_analytics_events_event_type", "analytics_events", ["event_type"])
    op.create_index("ix_analytics_events_post_id", "analytics_events", ["post_id"])
    op.create_index("ix_analytics_events_target_user_id", "analytics_events", ["target_user_id"])
    op.create_index("ix_analytics_events_session_id", "analytics_events", ["session_id"])
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_analytics_events_created_at", table_name="analytics_events")
    op.drop_index("ix_analytics_events_session_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_target_user_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_post_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_event_type", table_name="analytics_events")
    op.drop_index("ix_analytics_events_user_id", table_name="analytics_events")
    op.drop_table("analytics_events")

    postgresql.ENUM(*EVENT_TYPES, name="analytics_event_type").drop(op.get_bind(), checkfirst=True)
