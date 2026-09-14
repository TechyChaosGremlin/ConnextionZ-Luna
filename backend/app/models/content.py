"""
Content models — Posts, Comments, and Media.

Covers the core content types for the platform feed and creator profiles.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


# ── Enums ────────────────────────────────────────────────────────


class ContentType(str, enum.Enum):
    POST = "post"
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    LIVE_STREAM = "live_stream"


class ContentStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    FLAGGED = "flagged"
    REMOVED = "removed"
    SCHEDULED = "scheduled"


# ── Post ─────────────────────────────────────────────────────────


class Post(Base, TimestampMixin, SoftDeleteMixin):
    """A content post — the primary unit of creator content."""

    __tablename__ = "posts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content
    # values_callable makes SQLAlchemy persist the enum *values* ("video",
    # "published") rather than the member *names* ("VIDEO", "PUBLISHED"),
    # matching the lowercase labels migration 001 created in the DB enums.
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ContentType.POST,
    )
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="content_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ContentStatus.DRAFT,
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    mentions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sound_track: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Metrics (denormalized)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    share_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    save_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Scheduling
    scheduled_at: Mapped[str | None] = mapped_column(
        String(64), nullable=True  # ISO 8601
    )
    published_at: Mapped[str | None] = mapped_column(
        String(64), nullable=True  # ISO 8601
    )

    # ── Ported from legacy Post (kept for frontend compatibility) ──
    thumbnail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    audio: Mapped[str] = mapped_column(String(255), default="Original Sound", nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)
    allow_comments: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_collabs: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duration_sec: Mapped[float] = mapped_column(default=0.0, nullable=False)
    collab_with: Mapped[str | None] = mapped_column(String(120), nullable=True)
    moderation_status: Mapped[str] = mapped_column(
        String(20), default="approved", nullable=False, index=True
    )

    # Relationships
    media: Mapped[list["Media"]] = relationship(
        "Media", back_populates="post", lazy="selectin", cascade="all, delete-orphan"
    )
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="post", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Post id={self.id!r} type={self.content_type.value!r} status={self.status.value!r}>"


# ── Comment ──────────────────────────────────────────────────────


class Comment(Base, TimestampMixin, SoftDeleteMixin):
    """A comment on a post — supports nested replies via parent_id."""

    __tablename__ = "comments"

    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Metrics
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Ported from legacy Comment (kept for frontend compatibility) ──
    moderation_status: Mapped[str] = mapped_column(
        String(20), default="approved", nullable=False, index=True
    )

    # Relationships
    post: Mapped["Post"] = relationship("Post", back_populates="comments")
    replies: Mapped[list["Comment"]] = relationship(
        "Comment", backref="parent", remote_side="Comment.id", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Comment id={self.id!r} post_id={self.post_id!r}>"


# ── Media ────────────────────────────────────────────────────────


class Media(Base, TimestampMixin, SoftDeleteMixin):
    """Media attached to a post — images, videos, audio files."""

    __tablename__ = "media"

    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # File info
    media_type: Mapped[str] = mapped_column(
        String(64), nullable=False  # "image/jpeg", "video/mp4", etc.
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)

    # Storage
    storage_provider: Mapped[str] = mapped_column(
        String(32), default="s3", nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Processing
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    post: Mapped["Post"] = relationship("Post", back_populates="media")

    def __repr__(self) -> str:
        return f"<Media id={self.id!r} type={self.media_type!r}>"