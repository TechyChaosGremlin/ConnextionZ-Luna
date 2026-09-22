"""Streaming feature database foundation — stream_sessions,
stream_destinations, connected_stream_accounts.

Revision ID: 197
Revises: 196
Create Date: 2026-09-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "197"
down_revision = "196"
branch_labels = None
depends_on = None


STREAM_SESSION_STATUSES = ("pending", "active", "ended", "failed")
STREAM_DESTINATION_STATUSES = ("pending", "connecting", "live", "ended", "failed")
STREAM_PLATFORMS = ("twitch", "youtube", "kick", "facebook")


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────
    stream_session_status_enum = postgresql.ENUM(
        *STREAM_SESSION_STATUSES, name="stream_session_status", create_type=True
    )
    stream_session_status_enum.create(op.get_bind(), checkfirst=True)

    stream_destination_status_enum = postgresql.ENUM(
        *STREAM_DESTINATION_STATUSES, name="stream_destination_status", create_type=True
    )
    stream_destination_status_enum.create(op.get_bind(), checkfirst=True)

    stream_platform_enum = postgresql.ENUM(
        *STREAM_PLATFORMS, name="stream_platform", create_type=True
    )
    stream_platform_enum.create(op.get_bind(), checkfirst=True)

    # ── stream_sessions ──────────────────────────────────────────
    op.create_table(
        "stream_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_source", sa.String(length=2048), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*STREAM_SESSION_STATUSES, name="stream_session_status", create_type=False),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_stream_sessions_owner_id", "stream_sessions", ["owner_id"])

    # ── connected_stream_accounts ────────────────────────────────
    op.create_table(
        "connected_stream_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(*STREAM_PLATFORMS, name="stream_platform", create_type=False),
            nullable=False,
        ),
        sa.Column("platform_user_id", sa.String(length=256), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "platform", "platform_user_id",
            name="uq_connected_stream_account_identity",
        ),
    )
    op.create_index("ix_connected_stream_accounts_user_id", "connected_stream_accounts", ["user_id"])
    op.create_index(
        "ix_connected_stream_accounts_platform_user",
        "connected_stream_accounts",
        ["platform", "platform_user_id"],
    )

    # ── stream_destinations ──────────────────────────────────────
    op.create_table(
        "stream_destinations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(*STREAM_PLATFORMS, name="stream_platform", create_type=False),
            nullable=False,
        ),
        sa.Column("connected_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*STREAM_DESTINATION_STATUSES, name="stream_destination_status", create_type=False),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["stream_session_id"], ["stream_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connected_account_id"], ["connected_stream_accounts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_stream_destinations_stream_session_id", "stream_destinations", ["stream_session_id"])
    op.create_index("ix_stream_destinations_connected_account_id", "stream_destinations", ["connected_account_id"])
    op.create_index("ix_stream_destinations_platform", "stream_destinations", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_stream_destinations_platform", table_name="stream_destinations")
    op.drop_index("ix_stream_destinations_connected_account_id", table_name="stream_destinations")
    op.drop_index("ix_stream_destinations_stream_session_id", table_name="stream_destinations")
    op.drop_table("stream_destinations")

    op.drop_index("ix_connected_stream_accounts_platform_user", table_name="connected_stream_accounts")
    op.drop_index("ix_connected_stream_accounts_user_id", table_name="connected_stream_accounts")
    op.drop_table("connected_stream_accounts")

    op.drop_index("ix_stream_sessions_owner_id", table_name="stream_sessions")
    op.drop_table("stream_sessions")

    postgresql.ENUM(*STREAM_PLATFORMS, name="stream_platform").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*STREAM_DESTINATION_STATUSES, name="stream_destination_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*STREAM_SESSION_STATUSES, name="stream_session_status").drop(op.get_bind(), checkfirst=True)
