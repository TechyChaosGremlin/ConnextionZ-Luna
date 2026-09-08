"""Add the 'not_interested' value to the signal_type enum (explicit negative
feedback from the viewer, emitted by the not_interested mutation).

Revision ID: 011
Revises: 010
Create Date: 2026-09-07 00:00:00.000000
"""

from alembic import op


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres ALTER TYPE ... ADD VALUE cannot run inside a transaction block
    # on older versions; use autocommit for the enum extensions.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE signal_type ADD VALUE IF NOT EXISTS 'not_interested'")
        op.execute("ALTER TYPE analytics_event_type ADD VALUE IF NOT EXISTS 'not_interested'")


def downgrade() -> None:
    # Postgres does not support removing a value from an enum type. Downgrade
    # recreates the enums without 'not_interested' and rewrites the columns.
    op.execute(
        "ALTER TYPE signal_type RENAME TO signal_type_old"
    )
    op.execute(
        """
        CREATE TYPE signal_type AS ENUM (
            'view','watch_duration','completion','rewatch','like','unlike',
            'save','unsave','share','follow','unfollow'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE interaction_signals
        ALTER COLUMN signal_type TYPE signal_type
        USING signal_type::text::signal_type
        """
    )
    op.execute("DROP TYPE signal_type_old")

    op.execute("ALTER TYPE analytics_event_type RENAME TO analytics_event_type_old")
    op.execute(
        """
        CREATE TYPE analytics_event_type AS ENUM (
            'video_impression','video_viewed','video_completed','video_skipped',
            'video_watched','like_created','like_removed','comment_created',
            'share_created','save_created','follow_created','follow_removed',
            'profile_viewed','video_uploaded','video_published','sound_used',
            'search_performed','collab_created','notification_opened'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE analytics_events
        ALTER COLUMN event_type TYPE analytics_event_type
        USING event_type::text::analytics_event_type
        """
    )
    op.execute("DROP TYPE analytics_event_type_old")
