"""Centralized analytics event tracking service (Event Tracking v1).

This is the ONLY supported way to record an ``AnalyticsEvent`` row.
Resolvers/routes must call ``AnalyticsEventService(...).track_event(...)``
instead of constructing ``AnalyticsEvent`` rows directly, so that:

- event types are validated against the canonical ``EventType`` enum
- nullable associations (user/post/target_user/session) are handled safely
- a failure recording analytics NEVER breaks the primary user action
- callers can opt into a SAVEPOINT so a bad event cannot poison the
  caller's in-flight transaction (enabled for stream-attributed follows)

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
from app.models.paid_delivery import PaidDelivery
from repositories.analytics_event_repository import AnalyticsEventRepository

logger = structlog.get_logger()

# Fields that must never end up in event metadata (defense in depth — callers
# should never pass these, but strip them if they somehow do).
_FORBIDDEN_METADATA_KEYS = {
    "password",
    "hashedpassword",
    "token",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "jwt",
    "creditcard",
    "cardnumber",
    "cvv",
    "ssn",
    "paidcampaignid",
    "paidcampaign",
    "campaignid",
    "campaign",
    "paiddeliveryid",
    "deliveryid",
    "selectionid",
    "impressionid",
    "sponsoredlabel",
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
    sanitized = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            continue
        normalized = key.replace("_", "").lower()
        if normalized in _FORBIDDEN_METADATA_KEYS or normalized.startswith("paid"):
            continue
        if normalized in {"source", "algorithm"} and "paid" in str(value).lower():
            continue
        if normalized in {"issponsored", "sponsored"}:
            continue
        sanitized[key] = value
    return sanitized or None


class AnalyticsEventService:
    """Records ``AnalyticsEvent`` rows. Never raises to the caller."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._repo = AnalyticsEventRepository(db)

    async def _paid_delivery_for(
        self, delivery_id: uuid.UUID, user_id: uuid.UUID, post_id: uuid.UUID
    ) -> PaidDelivery:
        from repositories.paid_delivery_repository import PaidDeliveryRepository

        async with self.db.begin_nested():
            return await PaidDeliveryRepository(self.db).impressed_for_engagement(
                delivery_id, user_id, post_id
            )

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
        isolate_failure: bool = False,
        paid_delivery_id: uuid.UUID | None = None,
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

        clean_metadata = _sanitize_metadata(metadata) or {}
        validated_paid_delivery_id = None
        paid_campaign_id = None
        if paid_delivery_id is not None:
            if user_id is None or post_id is None:
                logger.warning("analytics_event.invalid_paid_attribution_context")
                return None
            try:
                delivery = await self._paid_delivery_for(paid_delivery_id, user_id, post_id)
            except Exception:
                logger.exception(
                    "analytics_event.paid_attribution_unavailable",
                    user_id=str(user_id),
                    post_id=str(post_id),
                )
                clean_metadata["attribution_status"] = "unavailable"
                return None
            else:
                validated_paid_delivery_id = delivery.id
                paid_campaign_id = delivery.campaign_id
                clean_metadata.update(
                    {
                        "source": "paid",
                        "paid_campaign_id": str(delivery.campaign_id),
                        "paid_delivery_id": str(delivery.id),
                    }
                )

        event = AnalyticsEvent(
            user_id=user_id,
            event_type=event_type,
            post_id=post_id,
            target_user_id=target_user_id,
            session_id=session_id,
            duration_ms=duration_ms,
            paid_delivery_id=validated_paid_delivery_id,
            paid_campaign_id=paid_campaign_id,
            event_metadata=clean_metadata or None,
        )

        try:
            if isolate_failure:
                async with self.db.begin_nested():
                    await self._repo.create_event(event)
            else:
                await self._repo.create_event(event)
        except Exception:
            logger.warning(
                "analytics_event.record_failed", event_type=event_type.value, exc_info=True
            )
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
