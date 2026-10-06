"""Persist messages posted in Luna stream chat.

Revision ID: 204
Revises: 203
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "204"
down_revision = "203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["stream_session_id"], ["stream_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_chat_messages_stream_created",
        "stream_chat_messages",
        ["stream_session_id", "created_at"],
    )
    op.create_index(
        "ix_stream_chat_messages_sender_stream",
        "stream_chat_messages",
        ["user_id", "stream_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_stream_chat_messages_sender_stream", table_name="stream_chat_messages")
    op.drop_index("ix_stream_chat_messages_stream_created", table_name="stream_chat_messages")
    op.drop_table("stream_chat_messages")
