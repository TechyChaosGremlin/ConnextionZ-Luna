"""
Social interaction models — ported from the legacy backend.

Covers Follows, per-post interactions (like/save/share/watch), comment
likes, creator playlists, trending sounds, and search history. These
back the feed/social features the frontend already depends on.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


# ── Follows ──────────────────────────────────────────────────────


class Follow(Base, TimestampMixin):
    """One user following another."""

    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("follower_id", "following_id", name="uq_follow_pair"),
    )

    follower_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    following_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class UserBlock(Base, TimestampMixin):
    """A directional block between two users."""

    __tablename__ = "user_blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_user_block_pair"),
    )

    blocker_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class UserMute(Base, TimestampMixin):
    """A directional mute between two users."""

    __tablename__ = "user_mutes"
    __table_args__ = (
        UniqueConstraint("muter_id", "muted_id", name="uq_user_mute_pair"),
    )

    muter_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    muted_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


# ── Post interactions ────────────────────────────────────────────


class PostLike(Base, TimestampMixin):
    __tablename__ = "post_likes"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_like"),
        Index("ix_post_likes_paid_post", "paid_delivery_id", "post_id"),
        CheckConstraint(
            "(paid_delivery_id IS NULL) = (paid_campaign_id IS NULL)",
            name="ck_post_likes_paid_attribution",
        ),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paid_delivery_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    paid_campaign_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class PostSave(Base, TimestampMixin):
    __tablename__ = "post_saves"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_save"),
        Index("ix_post_saves_paid_post", "paid_delivery_id", "post_id"),
        CheckConstraint(
            "(paid_delivery_id IS NULL) = (paid_campaign_id IS NULL)",
            name="ck_post_saves_paid_attribution",
        ),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paid_delivery_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    paid_campaign_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class PostShare(Base, TimestampMixin):
    __tablename__ = "post_shares"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_share"),
        Index("ix_post_shares_paid_post", "paid_delivery_id", "post_id"),
        CheckConstraint(
            "(paid_delivery_id IS NULL) = (paid_campaign_id IS NULL)",
            name="ck_post_shares_paid_attribution",
        ),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paid_delivery_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    paid_campaign_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class PostWatch(Base, TimestampMixin):
    """One watch event per call — rewatch flag distinguishes repeats, rows are never merged."""

    __tablename__ = "post_watches"
    __table_args__ = (
        Index("ix_post_watches_paid_post", "paid_delivery_id", "post_id"),
        CheckConstraint(
            "(paid_delivery_id IS NULL) = (paid_campaign_id IS NULL)",
            name="ck_post_watches_paid_attribution",
        ),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    watched_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rewatched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paid_delivery_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    paid_campaign_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class CommentLike(Base, TimestampMixin):
    __tablename__ = "comment_likes"
    __table_args__ = (UniqueConstraint("comment_id", "user_id", name="uq_comment_like"),)

    comment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


# ── Playlists ────────────────────────────────────────────────────


class Playlist(Base, TimestampMixin):
    __tablename__ = "playlists"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    cover: Mapped[str] = mapped_column(String(500), nullable=False)
    item_label: Mapped[str] = mapped_column(String(80), nullable=False)
    plays: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ── Search ───────────────────────────────────────────────────────


class SearchQuery(Base, TimestampMixin):
    __tablename__ = "search_queries"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    query_text: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_query: Mapped[str] = mapped_column(String(200), nullable=False, index=True)


# ── Sounds ───────────────────────────────────────────────────────


class Sound(Base, TimestampMixin):
    __tablename__ = "sounds"

    title: Mapped[str] = mapped_column(String(150), nullable=False)
    creator: Mapped[str] = mapped_column(String(120), nullable=False)
    creator_avatar: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artwork: Mapped[str] = mapped_column(Text, nullable=False, default="")
    genre: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    video_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_plays: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    growth_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration: Mapped[str] = mapped_column(String(20), default="0:30", nullable=False)
    bpm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
