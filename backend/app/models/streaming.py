"""
Luna streaming feature — database foundation.

Covers the multi-destination live streaming MVP models:
- ``StreamSession`` — one broadcast attempt owned by a user (input source,
  lifecycle status, failure reason).
- ``StreamDestination`` — one output target (Twitch/YouTube/Kick/Facebook)
  attached to a ``StreamSession``, optionally backed by a
  ``ConnectedStreamAccount``.
- ``ConnectedStreamAccount`` — a user's linked OAuth account on a streaming
  platform, used to authorize destinations.

This module intentionally contains ONLY schema/model definitions — no
FFmpeg/process management, no OAuth flow, no API routes.

SECURITY NOTE (flagged for the next implementation step): ``access_token``
and ``refresh_token`` on ``ConnectedStreamAccount`` are stored as opaque
``Text`` columns. Luna has no existing encrypted-secret-at-rest mechanism
(``SecretStr`` in ``app/config.py`` only protects settings from accidental
logging/repr, not database storage). Do NOT log these columns, and do NOT
serialize them in any GraphQL type/response. A proper encryption-at-rest
mechanism (e.g. application-level envelope encryption or pgcrypto) must be
added before real OAuth tokens are persisted here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


# ── Enums ────────────────────────────────────────────────────────


class StreamPlatform(str, enum.Enum):
    """Supported streaming destination platforms."""

    TWITCH = "twitch"
    YOUTUBE = "youtube"
    KICK = "kick"
    FACEBOOK = "facebook"


class StreamSessionStatus(str, enum.Enum):
    """Lifecycle of a broadcast attempt."""

    PENDING = "pending"
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class StreamDestinationStatus(str, enum.Enum):
    """Lifecycle of a single output destination within a session."""

    PENDING = "pending"
    CONNECTING = "connecting"
    LIVE = "live"
    ENDED = "ended"
    FAILED = "failed"


# ── StreamSession ────────────────────────────────────────────────


class StreamSession(Base, TimestampMixin):
    """A single broadcast attempt owned by a user."""

    __tablename__ = "stream_sessions"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    input_source: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[StreamSessionStatus] = mapped_column(
        Enum(
            StreamSessionStatus,
            name="stream_session_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=StreamSessionStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    owner: Mapped["User"] = relationship("User", back_populates="stream_sessions")  # noqa: F821
    destinations: Mapped[list["StreamDestination"]] = relationship(
        "StreamDestination", back_populates="stream_session", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<StreamSession id={self.id!r} owner_id={self.owner_id!r} status={self.status.value!r}>"


# ── StreamDestination ────────────────────────────────────────────


class StreamDestination(Base, TimestampMixin):
    """A single output destination (platform target) for a stream session."""

    __tablename__ = "stream_destinations"

    stream_session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stream_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[StreamPlatform] = mapped_column(
        Enum(
            StreamPlatform,
            name="stream_platform",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    connected_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("connected_stream_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[StreamDestinationStatus] = mapped_column(
        Enum(
            StreamDestinationStatus,
            name="stream_destination_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=StreamDestinationStatus.PENDING,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    stream_session: Mapped["StreamSession"] = relationship(
        "StreamSession", back_populates="destinations"
    )
    connected_account: Mapped["ConnectedStreamAccount | None"] = relationship(
        "ConnectedStreamAccount", back_populates="destinations"
    )

    def __repr__(self) -> str:
        return (
            f"<StreamDestination id={self.id!r} platform={self.platform.value!r} "
            f"status={self.status.value!r}>"
        )


# ── ConnectedStreamAccount ───────────────────────────────────────


class ConnectedStreamAccount(Base, TimestampMixin):
    """A user's linked OAuth account on a streaming platform.

    ``access_token``/``refresh_token`` are opaque credential blobs — see the
    module-level SECURITY NOTE above. Never log or serialize them.
    """

    __tablename__ = "connected_stream_accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", "platform_user_id",
            name="uq_connected_stream_account_identity",
        ),
        Index("ix_connected_stream_accounts_platform_user", "platform", "platform_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[StreamPlatform] = mapped_column(
        Enum(
            StreamPlatform,
            name="stream_platform",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    platform_user_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)

    # Credentials — never expose via API responses or logs.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Column name stays "metadata" in the DB; the Python attribute is renamed
    # to avoid clashing with SQLAlchemy's reserved ``Base.metadata`` class attr.
    account_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="connected_stream_accounts")  # noqa: F821
    destinations: Mapped[list["StreamDestination"]] = relationship(
        "StreamDestination", back_populates="connected_account"
    )

    def __repr__(self) -> str:
        return (
            f"<ConnectedStreamAccount id={self.id!r} user_id={self.user_id!r} "
            f"platform={self.platform.value!r}>"
        )
