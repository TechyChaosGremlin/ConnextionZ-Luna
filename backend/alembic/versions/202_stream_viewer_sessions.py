"""Authenticated stream viewer-session persistence foundation.

Revision ID: 202
Revises: 201
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202"
down_revision = "201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_viewer_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["stream_session_id"], ["stream_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "stream_session_id", "user_id", "client_session_id",
            name="uq_stream_viewer_sessions_client_attempt",
        ),
        sa.CheckConstraint(
            "lease_expires_at > joined_at",
            name="ck_stream_viewer_sessions_lease_after_join",
        ),
        sa.CheckConstraint(
            "left_at IS NULL OR (left_at >= joined_at AND left_at <= lease_expires_at)",
            name="ck_stream_viewer_sessions_leave_within_lease",
        ),
    )
    op.create_index("ix_stream_viewer_sessions_user_id", "stream_viewer_sessions", ["user_id"])
    op.create_index(
        "ix_stream_viewer_sessions_open_lease",
        "stream_viewer_sessions",
        ["stream_session_id", "lease_expires_at"],
        postgresql_where=sa.text("left_at IS NULL"),
        sqlite_where=sa.text("left_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_stream_viewer_sessions_open_lease", table_name="stream_viewer_sessions")
    op.drop_index("ix_stream_viewer_sessions_user_id", table_name="stream_viewer_sessions")
    op.drop_table("stream_viewer_sessions")
