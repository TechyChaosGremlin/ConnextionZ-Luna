"""Read/write access for the general-purpose ``analytics_events`` log.

Writes should go through ``services.analytics_event_service.AnalyticsEventService``,
not this repository directly, so validation and failure-isolation are applied
consistently. This repository exists for the (currently internal-only) read
helpers and to keep the write path consistent with the rest of the codebase's
repository pattern.
"""

from __future__ import annotations

import uuid

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent, EventType
from app.models.content import Post
from repositories.base import BaseRepository


class AnalyticsEventRepository(BaseRepository[AnalyticsEvent]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, AnalyticsEvent)

    async def create_event(self, event: AnalyticsEvent) -> AnalyticsEvent:
        self.db.add(event)
        await self.db.flush()
        return event

    async def get_for_user(self, user_id: uuid.UUID, limit: int = 500) -> list[AnalyticsEvent]:
        result = await self.db.execute(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.user_id == user_id)
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_for_post(self, post_id: uuid.UUID, limit: int = 500) -> list[AnalyticsEvent]:
        result = await self.db.execute(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.post_id == post_id)
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_type(self, event_type: EventType, limit: int = 500) -> list[AnalyticsEvent]:
        result = await self.db.execute(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.event_type == event_type)
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def profile_viewer_counts_for_creator(
        self, creator_id: uuid.UUID, start, end
    ) -> dict[str, int]:
        result = await self.db.execute(
            select(
                func.count().label("total"),
                func.count(distinct(AnalyticsEvent.user_id)).label("unique_viewers"),
            ).select_from(AnalyticsEvent).where(
                AnalyticsEvent.event_type == EventType.PROFILE_VIEWED,
                AnalyticsEvent.target_user_id == creator_id,
                AnalyticsEvent.created_at >= start,
                AnalyticsEvent.created_at <= end,
            )
        )
        row = result.one()
        return {
            "total": int(row.total or 0),
            "unique_viewers": int(row.unique_viewers or 0),
        }

    async def post_event_totals_for_creator(
        self, creator_id: uuid.UUID, start, end
    ) -> dict[EventType, dict[str, int]]:
        result = await self.db.execute(
            select(
                AnalyticsEvent.event_type,
                func.count().label("count"),
                func.count(distinct(AnalyticsEvent.user_id)).label("unique_users"),
            )
            .select_from(AnalyticsEvent)
            .join(Post, Post.id == AnalyticsEvent.post_id)
            .where(
                AnalyticsEvent.event_type.in_(
                    [
                        EventType.VIDEO_IMPRESSION,
                        EventType.VIDEO_VIEWED,
                        EventType.VIDEO_SKIPPED,
                    ]
                ),
                Post.user_id == creator_id,
                AnalyticsEvent.created_at >= start,
                AnalyticsEvent.created_at <= end,
            )
            .group_by(AnalyticsEvent.event_type)
        )
        return {
            row.event_type: {
                "count": int(row.count or 0),
                "unique_users": int(row.unique_users or 0),
            }
            for row in result.all()
        }
