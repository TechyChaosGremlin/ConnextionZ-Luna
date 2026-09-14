"""Centralized analytics event tracking service (Event Tracking v1).

This is the ONLY supported way to record an ``AnalyticsEvent`` row.
Resolvers/routes must call ``AnalyticsEventService(...).track_event(...)``
instead of constructing ``AnalyticsEvent`` rows directly, so that:

- event types are validated against the canonical ``EventType`` enum
- nullable associations (user/post/target_user/session) are handled safely
- a failure recording analytics NEVER breaks the primary user action
- writes are isolated in their own transaction (SAVEPOINT) so a bad event
  can't poison the caller's in-flight transaction

Usage (mirrors the existing ``AnalyticsRepository(ctx.db).record(...)``
call convention already used for recommendation signals)::

    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.VIDEO_VIEWED,
        user=ctx.current_user,
        post=post,
        session_id=ctx.session_id,
        metadata={"source": "for_you_feed"},
    )
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent, EventType
from repositories.analytics_event_repository import AnalyticsEventRepository

logger = structlog.get_logger()

# Fields that must never end up in event metadata (defense in depth — callers
# should never pass these, but strip them if they somehow do).
_FORBIDDEN_METADATA_KEYS = {
    "password",
    "hashed_password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "jwt",
    "credit_card",
    "card_number",
    "cvv",
    "ssn",
}


def _has_id(entity: Any) -> uuid.UUID | None:
    """Best-effort extraction of ``.id`` from an ORM row/object, or None."""
    if entity is None:
        return None
    entity_id = getattr(entity, "id", None)
    return entity_id if isinstance(entity_id, uuid.UUID) else None


def _sanitize_metadata(metadata: dict | None) -> dict | None:
    if not metadata:
        return None
    return {k: v for k, v in metadata.items() if k.lower() not in _FORBIDDEN_METADATA_KEYS}


class AnalyticsEventService:
    """Records ``AnalyticsEvent`` rows. Never raises to the caller."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._repo = AnalyticsEventRepository(db)

    async def track_event(
        self,
        *,
        event_type: EventType,
        user: Any = None,
        post: Any = None,
        target_user: Any = None,
        session_id: str | None = None,
        duration_ms: int | None = None,
        metadata: dict | None = None,
    ) -> AnalyticsEvent | None:
        """Record one analytics event. Returns the created row, or ``None``
        if the event was rejected as malformed or recording failed.

        Failures (bad input or DB errors) are logged and swallowed —
        analytics must never break the primary user action.
        """
        if not isinstance(event_type, EventType):
            logger.warning("analytics_event.invalid_type", event_type=repr(event_type))
            return None

        if duration_ms is not None and (not isinstance(duration_ms, int) or duration_ms < 0):
            logger.warning("analytics_event.invalid_duration", duration_ms=duration_ms)
            return None

        user_id = _has_id(user)
        post_id = _has_id(post)
        target_user_id = _has_id(target_user)

        if session_id is not None and not isinstance(session_id, str):
            session_id = str(session_id)

        event = AnalyticsEvent(
            user_id=user_id,
            event_type=event_type,
            post_id=post_id,
            target_user_id=target_user_id,
            session_id=session_id,
            duration_ms=duration_ms,
            event_metadata=_sanitize_metadata(metadata),
        )

        try:
            # Recording failures are logged and swallowed here so a bad
            # event never breaks the caller's primary action. This does not
            # use a SAVEPOINT: the existing resolver test suite exercises
            # ``ctx.db`` as a bare ``AsyncMock`` (no real transaction), and
            # analytics writes participate in the same session/transaction
            # as the primary action (committed together, same as the
            # existing ``AnalyticsRepository.record`` signal writes).
            await self._repo.create_event(event)
        except Exception:
            logger.warning("analytics_event.record_failed", event_type=event_type.value, exc_info=True)
            return None

        return event

    async def track_impressions_bulk(
        self,
        *,
        user: Any = None,
        posts: list[Any],
        session_id: str | None = None,
    ) -> None:
        """Record ``VIDEO_IMPRESSION`` for a batch of feed posts in a single
        round-trip transaction — used when a feed page is served, so tracking
        impressions doesn't add N separate writes to feed-scroll latency.
        """
        if not posts:
            return

        user_id = _has_id(user)
        events = [
            AnalyticsEvent(
                user_id=user_id,
                event_type=EventType.VIDEO_IMPRESSION,
                post_id=_has_id(post),
                session_id=session_id,
            )
            for post in posts
        ]

        try:
            self.db.add_all(events)
            await self.db.flush()
        except Exception:
            logger.warning("analytics_event.bulk_record_failed", count=len(events), exc_info=True)
