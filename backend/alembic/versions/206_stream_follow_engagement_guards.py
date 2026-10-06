"""Guard native stream follow attribution and index creator reporting.

Revision ID: 206
Revises: 205
"""

from alembic import op
import sqlalchemy as sa

revision = "206"
down_revision = "205"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_interaction_signals_stream_follow",
        "interaction_signals",
        "stream_session_id IS NULL OR (signal_type = 'follow' AND post_id IS NULL)",
    )
    op.create_index(
        "ix_interaction_signals_creator_stream_period",
        "interaction_signals",
        ["creator_id", "created_at"],
        postgresql_where=sa.text("stream_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_interaction_signals_creator_stream_period", table_name="interaction_signals")
    op.drop_constraint("ck_interaction_signals_stream_follow", "interaction_signals", type_="check")
