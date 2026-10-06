"""Persist native Luna stream subscription actions.

Revision ID: 205
Revises: 204
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "205"
down_revision = "204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["stream_session_id"], ["stream_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stream_session_id", "user_id", name="uq_stream_subscriptions_stream_user"
        ),
    )
    op.create_index(
        "ix_stream_subscriptions_stream_created",
        "stream_subscriptions",
        ["stream_session_id", "created_at"],
    )
    op.create_index("ix_stream_subscriptions_user", "stream_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_stream_subscriptions_user", table_name="stream_subscriptions")
    op.drop_index("ix_stream_subscriptions_stream_created", table_name="stream_subscriptions")
    op.drop_table("stream_subscriptions")
