"""
User, Profile, and Session models.

Covers:
- ``User`` — core identity with auth fields (email, hashed_password, role)
- ``Profile`` — public-facing creator profile (display_name, bio, avatar, etc.)
- ``Session`` — Redis-backed session tracking (DB table for audit/reference)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


# ── Enums ────────────────────────────────────────────────────────


class UserRole(str, enum.Enum):
    """Role hierarchy: admin > creator > user > guest."""

    ADMIN = "admin"
    CREATOR = "creator"
    USER = "user"
    GUEST = "guest"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BANNED = "banned"
    PENDING_VERIFICATION = "pending_verification"


# ── User ─────────────────────────────────────────────────────────


class User(Base, TimestampMixin, SoftDeleteMixin):
    """Core user identity — authentication, authorization, and account state."""

    __tablename__ = "users"

    # Identity
    email: Mapped[str] = mapped_column(
        String(320), unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)

    # Role & Status
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=UserRole.USER,
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(
            AccountStatus,
            name="account_status",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=AccountStatus.PENDING_VERIFICATION,
    )

    # Verification & Security
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Relationships
    profile: Mapped["Profile | None"] = relationship(
        "Profile", back_populates="user", uselist=False, lazy="selectin"
    )
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", lazy="selectin"
    )
    stream_sessions: Mapped[list["StreamSession"]] = relationship(  # noqa: F821
        "StreamSession", back_populates="owner"
    )
    connected_stream_accounts: Mapped[list["ConnectedStreamAccount"]] = relationship(  # noqa: F821
        "ConnectedStreamAccount", back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r} role={self.role.value!r}>"


# ── Profile ──────────────────────────────────────────────────────


class Profile(Base, TimestampMixin, SoftDeleteMixin):
    """Public-facing creator profile — one-to-one with User."""

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Display
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Creator metadata
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    social_links: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Metrics (denormalized for fast reads)
    follower_count: Mapped[int] = mapped_column(default=0, nullable=False)
    following_count: Mapped[int] = mapped_column(default=0, nullable=False)
    collaboration_count: Mapped[int] = mapped_column(default=0, nullable=False)
    total_likes: Mapped[int] = mapped_column(default=0, nullable=False)

    # ── Ported from legacy Profile (kept for frontend compatibility) ──
    avatar_color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    collab_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collab_score: Mapped[float] = mapped_column(default=0.0, nullable=False)
    open_to_collab: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    private_account: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    response_time: Mapped[str] = mapped_column(String(50), default="< 4 hours", nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="profile")

    def __repr__(self) -> str:
        return f"<Profile id={self.id!r} display_name={self.display_name!r}>"


# ── Session ──────────────────────────────────────────────────────


class Session(Base, TimestampMixin):
    """Persistent session record for audit and token management.

    The actual session data lives in Redis; this table provides
    a durable reference for token blacklisting and audit trails.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Token tracking
    refresh_token_jti: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    access_token_jti: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Lifecycle
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Context
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    @property
    def is_active(self) -> bool:
        """A session is active if not revoked and not expired."""
        now = datetime.now(tz=self.expires_at.tzinfo)
        return self.revoked_at is None and self.expires_at > now

    def __repr__(self) -> str:
        return f"<Session id={self.id!r} user_id={self.user_id!r} active={self.is_active!r}>"