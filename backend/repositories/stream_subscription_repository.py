"""Persist native Luna stream subscription actions and aggregate creator totals."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.errors import ForbiddenError, NotFoundError
from app.models.streaming import StreamSession, StreamSubscription
from repositories.base import BaseRepository


class StreamSubscriptionRepository(BaseRepository[StreamSubscription]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, StreamSubscription)

    async def subscribe(
        self, *, stream_id: uuid.UUID, creator_id: uuid.UUID, user_id: uuid.UUID
    ) -> StreamSubscription:
        result = await self.db.execute(
            select(StreamSession)
            .options(lazyload(StreamSession.destinations))
            .where(StreamSession.id == stream_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stream = result.scalar_one_or_none()
        if stream is None:
            raise NotFoundError("Stream not found")
        if stream.owner_id != creator_id:
            raise ForbiddenError("Stream is not owned by the subscription recipient")
        if user_id == creator_id:
            raise ForbiddenError("You cannot subscribe to your own stream")

        dialect = self.db.get_bind().dialect.name
        if dialect == "postgresql":
            statement = pg_insert(StreamSubscription)
        elif dialect == "sqlite":
            statement = sqlite_insert(StreamSubscription)
        else:
            raise NotImplementedError(
                f"Stream subscriptions do not support database dialect {dialect}"
            )
        result = await self.db.execute(
            statement.values(stream_session_id=stream_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["stream_session_id", "user_id"])
            .returning(StreamSubscription)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            result = await self.db.execute(
                select(StreamSubscription).where(
                    StreamSubscription.stream_session_id == stream_id,
                    StreamSubscription.user_id == user_id,
                )
            )
            subscription = result.scalar_one_or_none()
        if subscription is None:
            raise RuntimeError("Subscription conflict did not resolve to a persisted subscription")
        return subscription

    async def creator_subscription_totals(
        self, *, creator_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Count actions and distinct subscribers across owned broadcasts in [start, end)."""
        if end <= start:
            raise ValueError("Reporting end must be after reporting start")
        result = await self.db.execute(
            select(
                func.count(StreamSubscription.id).label("subscriptions"),
                func.count(func.distinct(StreamSubscription.user_id)).label("subscribers"),
            )
            .select_from(StreamSubscription)
            .join(StreamSession, StreamSession.id == StreamSubscription.stream_session_id)
            .where(
                StreamSession.owner_id == creator_id,
                StreamSubscription.created_at >= start,
                StreamSubscription.created_at < end,
            )
        )
        row = result.one()
        return {
            "stream_subscriptions": int(row.subscriptions),
            "unique_stream_subscribers": int(row.subscribers),
        }
