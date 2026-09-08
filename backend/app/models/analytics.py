"""
Recommendation-ready analytics — a unified, append-only log of engagement
signals across posts and creators.

The existing tables (``post_likes``, ``post_saves``, ``post_shares``,
``post_watches``, ``follows``) remain the source of truth for idempotent
toggle state and denormalized counters. ``InteractionSignal`` complements
them with a single flat event stream purpose-built for recommendation
feature extraction (e.g. "posts this user watched to completion",
"creators this user engages with most"), so a feature pipeline doesn't
need to join across five different tables.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SignalType(str, enum.Enum):
    """Recommendation-relevant engagement signal kinds."""

    VIEW = "view"
    WATCH_DURATION = "watch_duration"
    COMPLETION = "completion"
    REWATCH = "rewatch"
    LIKE = "like"
    UNLIKE = "unlike"
    SAVE = "save"
    UNSAVE = "unsave"
    SHARE = "share"
    FOLLOW = "follow"
    UNFOLLOW = "unfollow"
    # Explicit negative feedback: the viewer chose "Not Interested" on a post.
    # Real production signal emitted by the not_interested mutation.
    NOT_INTERESTED = "not_interested"


class InteractionSignal(Base, TimestampMixin):
    """One row per recommendation-relevant engagement event.

    ``post_id`` is null for creator-level signals (follow/unfollow).
    ``creator_id`` is the content owner for post signals, or the followed
    user for follow signals — always present so creator-affinity features
    can be computed without joining back to ``posts``.
    ``value`` carries the signal's magnitude where relevant (e.g. watched
    seconds for ``WATCH_DURATION``, 1.0 otherwise).
    """

    __tablename__ = "interaction_signals"
    __table_args__ = (
        Index("ix_interaction_signals_user_type", "user_id", "signal_type"),
        Index("ix_interaction_signals_post_type", "post_id", "signal_type"),
        Index("ix_interaction_signals_creator_type", "creator_id", "signal_type"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_type: Mapped[SignalType] = mapped_column(
        # values_callable persists the enum values ("view", ...) rather than
        # member names ("VIEW", ...), matching migration 004's DB enum labels.
        Enum(SignalType, name="signal_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    value: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


# ── Analytics Event Tracking (Event Tracking v1) ────────────────────────────
#
# ``AnalyticsEvent`` is a general-purpose, append-only product-analytics
# event log — broader in scope than ``InteractionSignal`` above (which is a
# narrow numeric feature stream purpose-built for recommendation scoring).
# ``AnalyticsEvent`` captures *any* trackable product interaction (video
# engagement, social actions, content lifecycle, search, notifications) with
# free-form JSON metadata, an optional session id, and duration in
# milliseconds, so it can power creator analytics, platform analytics, and
# the recommendation engine later without needing a schema change per
# feature. Recording is centralized through
# ``services.analytics_event_service.AnalyticsEventService`` — do not
# construct rows directly from resolvers/routes.


class EventType(str, enum.Enum):
    """Canonical analytics event types. Add new types here only."""

    VIDEO_IMPRESSION = "video_impression"
    VIDEO_VIEWED = "video_viewed"
    VIDEO_COMPLETED = "video_completed"
    VIDEO_SKIPPED = "video_skipped"
    VIDEO_WATCHED = "video_watched"
    LIKE_CREATED = "like_created"
    LIKE_REMOVED = "like_removed"
    COMMENT_CREATED = "comment_created"
    SHARE_CREATED = "share_created"
    SAVE_CREATED = "save_created"
    FOLLOW_CREATED = "follow_created"
    FOLLOW_REMOVED = "follow_removed"
    PROFILE_VIEWED = "profile_viewed"
    VIDEO_UPLOADED = "video_uploaded"
    VIDEO_PUBLISHED = "video_published"
    SOUND_USED = "sound_used"
    SEARCH_PERFORMED = "search_performed"
    COLLAB_CREATED = "collab_created"
    NOTIFICATION_OPENED = "notification_opened"
    NOT_INTERESTED = "not_interested"


class AnalyticsEvent(Base, TimestampMixin):
    """One row per tracked product-analytics event.

    ``user_id`` is nullable for anonymous events. ``post_id`` and
    ``target_user_id`` are nullable and only populated when relevant to the
    event type (e.g. ``target_user_id`` for follow/profile-view events).
    ``metadata`` is free-form JSON and must never contain sensitive data
    (passwords, tokens, payment info, message contents, raw request bodies).
    """

    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_analytics_events_created_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[EventType] = mapped_column(
        # Persist lowercase enum values, matching migration 008's DB labels.
        Enum(EventType, name="analytics_event_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        index=True,
    )
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Column name stays "metadata" in the DB; the Python attribute is renamed
    # to avoid clashing with SQLAlchemy's reserved ``Base.metadata`` class attr.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
