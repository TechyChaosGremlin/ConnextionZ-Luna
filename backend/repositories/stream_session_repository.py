"""Queries for persisted multi-destination streaming sessions."""

import uuid
from datetime import datetime
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.streaming import (
    LIVE_STREAM_OWNER_INDEX,
    StreamDestination,
    StreamPlatform,
    StreamSession,
    StreamSessionStatus,
)
from app.rate_limits import ActionRateLimitExceeded, STREAM_CONCURRENT_RETRY_SECONDS
from repositories.base import BaseRepository


class StreamDestinationCount(TypedDict):
    platform: StreamPlatform
    ended_sessions: int


class StreamSessionRepository(BaseRepository[StreamSession]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, StreamSession)

    async def reserve_live_slot(self, stream: StreamSession) -> None:
        """Flush the pending session before any process starts; the DB arbitrates races."""
        self.db.add(stream)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            original = exc.orig
            diagnostic = getattr(original, "diag", None)
            constraint = getattr(diagnostic, "constraint_name", None) or getattr(
                getattr(original, "__cause__", None), "constraint_name", None
            )
            if constraint == LIVE_STREAM_OWNER_INDEX or str(original) == (
                "UNIQUE constraint failed: stream_sessions.owner_id"
            ):
                raise ActionRateLimitExceeded(STREAM_CONCURRENT_RETRY_SECONDS) from exc
            raise

    async def get_active(self, limit: int = 20) -> list[StreamSession]:
        result = await self.db.execute(
            select(StreamSession)
            .where(StreamSession.status == StreamSessionStatus.ACTIVE)
            .order_by(StreamSession.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_ended_for_owner_in_period(
        self, owner_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[StreamSession]:
        result = await self.db.execute(
            select(StreamSession).where(
                StreamSession.owner_id == owner_id,
                StreamSession.status == StreamSessionStatus.ENDED,
                StreamSession.started_at.is_not(None),
                StreamSession.ended_at.is_not(None),
                StreamSession.ended_at >= start,
                StreamSession.ended_at <= end,
            )
        )
        return list(result.scalars().all())

    async def get_for_owner(self, owner_id: uuid.UUID) -> list[StreamSession]:
        result = await self.db.execute(
            select(StreamSession)
            .options(selectinload(StreamSession.destinations))
            .where(StreamSession.owner_id == owner_id)
            .order_by(StreamSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def ended_destination_counts_for_owner_in_period(
        self, owner_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[StreamDestinationCount]:
        result = await self.db.execute(
            select(
                StreamDestination.platform,
                func.count(func.distinct(StreamSession.id)).label("ended_sessions"),
            )
            .join(StreamSession, StreamSession.id == StreamDestination.stream_session_id)
            .where(
                StreamSession.owner_id == owner_id,
                StreamSession.status == StreamSessionStatus.ENDED,
                StreamSession.started_at.is_not(None),
                StreamSession.ended_at.is_not(None),
                StreamSession.ended_at >= start,
                StreamSession.ended_at <= end,
                StreamSession.ended_at >= StreamSession.started_at,
            )
            .group_by(StreamDestination.platform)
        )
        return sorted(
            [
                {"platform": row.platform, "ended_sessions": int(row.ended_sessions)}
                for row in result.all()
            ],
            key=lambda row: row["platform"].value,
        )