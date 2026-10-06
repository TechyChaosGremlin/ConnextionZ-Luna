"""Authenticated native stream subscription writes using the streaming action limiter."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.rate_limits import (
    STREAM_SUBSCRIBE_ACTION,
    STREAM_VIEWER_ACTION_LIMITS,
    ActionRateLimiter,
    client_identity,
)
from features.streaming.schemas import StreamSubscriptionResponse
from repositories.stream_subscription_repository import StreamSubscriptionRepository

stream_subscription_action_limiter = ActionRateLimiter(STREAM_VIEWER_ACTION_LIMITS)


class StreamSubscriptionService:
    def __init__(self, db: AsyncSession) -> None:
        self.repository = StreamSubscriptionRepository(db)

    async def subscribe(
        self, stream_id: uuid.UUID, creator_id: uuid.UUID, subscriber: User
    ) -> StreamSubscriptionResponse:
        stream_subscription_action_limiter.consume(
            client_identity(subscriber.id, ""), {STREAM_SUBSCRIBE_ACTION: 1}
        )
        subscription = await self.repository.subscribe(
            stream_id=stream_id, creator_id=creator_id, user_id=subscriber.id
        )
        return StreamSubscriptionResponse(
            id=subscription.id,
            stream_id=subscription.stream_session_id,
            user_id=subscription.user_id,
            creator_id=creator_id,
            created_at=subscription.created_at,
        )
