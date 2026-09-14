"""Read/write access for the general-purpose ``analytics_events`` log.

Writes should go through ``services.analytics_event_service.AnalyticsEventService``,
not this repository directly, so validation and failure-isolation are applied
consistently. This repository exists for the (currently internal-only) read
helpers and to keep the write path consistent with the rest of the codebase's
repository pattern.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent, EventType
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
