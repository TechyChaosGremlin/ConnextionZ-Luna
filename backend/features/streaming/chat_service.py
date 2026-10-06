"""Authenticated stream-chat writes backed by persisted active participation."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.rate_limits import (
    STREAM_CHAT_SEND_ACTION,
    STREAM_VIEWER_ACTION_LIMITS,
    ActionRateLimiter,
    client_identity,
)
from features.streaming.schemas import StreamChatMessageResponse
from repositories.stream_chat_repository import StreamChatRepository


stream_chat_action_limiter = ActionRateLimiter(STREAM_VIEWER_ACTION_LIMITS)


class StreamChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.repository = StreamChatRepository(db)

    async def send(
        self, stream_id: uuid.UUID, body: str, sender: User
    ) -> StreamChatMessageResponse:
        stream_chat_action_limiter.consume(
            client_identity(sender.id, ""), {STREAM_CHAT_SEND_ACTION: 1}
        )
        message = await self.repository.create_for_participant(
            stream_id=stream_id,
            user_id=sender.id,
            body=body.strip(),
            at=datetime.now(timezone.utc),
        )
        return StreamChatMessageResponse(
            id=message.id,
            stream_id=message.stream_session_id,
            user_id=message.user_id,
            body=message.body,
            created_at=message.created_at,
        )
