"""Persistence and creator analytics for Luna stream chat."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.streaming import (
    StreamChatMessage,
    StreamSession,
    StreamSessionStatus,
    StreamViewerSession,
)
from repositories.base import BaseRepository


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


class StreamChatRepository(BaseRepository[StreamChatMessage]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, StreamChatMessage)

    async def create_for_participant(
        self,
        *,
        stream_id: uuid.UUID,
        user_id: uuid.UUID,
        body: str,
        at: datetime,
    ) -> StreamChatMessage:
        stream_result = await self.db.execute(
            select(StreamSession)
            .options(lazyload(StreamSession.destinations))
            .where(StreamSession.id == stream_id)
            .with_for_update()
        )
        stream = stream_result.scalar_one_or_none()
        if stream is None:
            raise NotFoundError("Stream not found")
        if (
            stream.status != StreamSessionStatus.ACTIVE
            or stream.started_at is None
            or _as_utc(stream.started_at) > at
            or (stream.ended_at is not None and _as_utc(stream.ended_at) <= at)
        ):
            raise ConflictError("Stream is not active")

        if stream.owner_id != user_id:
            viewer_result = await self.db.execute(
                select(StreamViewerSession.id)
                .where(
                    StreamViewerSession.stream_session_id == stream_id,
                    StreamViewerSession.user_id == user_id,
                    StreamViewerSession.joined_at <= at,
                    StreamViewerSession.lease_expires_at > at,
                    or_(
                        StreamViewerSession.left_at.is_(None),
                        StreamViewerSession.left_at > at,
                    ),
                )
                .limit(1)
            )
            if viewer_result.scalar_one_or_none() is None:
                raise ForbiddenError("Join the active stream before sending chat messages")

        message = StreamChatMessage(
            stream_session_id=stream_id,
            user_id=user_id,
            body=body,
            created_at=at,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def count_for_stream(self, stream_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count(StreamChatMessage.id)).where(
                StreamChatMessage.stream_session_id == stream_id
            )
        )
        return int(result.scalar_one() or 0)

    async def creator_chat_totals(
        self, *, creator_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Count messages and distinct chatters from this creator's streams in [start, end)."""
        if end <= start:
            raise ValueError("Reporting end must be after reporting start")
        result = await self.db.execute(
            select(
                func.count(StreamChatMessage.id).label("messages"),
                func.count(func.distinct(StreamChatMessage.user_id)).label("chatters"),
            )
            .select_from(StreamChatMessage)
            .join(StreamSession, StreamSession.id == StreamChatMessage.stream_session_id)
            .where(
                StreamSession.owner_id == creator_id,
                StreamSession.status.in_(
                    (
                        StreamSessionStatus.ACTIVE,
                        StreamSessionStatus.ENDED,
                        StreamSessionStatus.FAILED,
                    )
                ),
                StreamSession.started_at.is_not(None),
                StreamSession.started_at <= StreamChatMessage.created_at,
                or_(
                    StreamSession.ended_at.is_(None),
                    StreamSession.ended_at > StreamChatMessage.created_at,
                ),
                StreamChatMessage.created_at >= start,
                StreamChatMessage.created_at < end,
            )
        )
        row = result.one()
        return {
            "stream_chat_messages": int(row.messages or 0),
            "unique_stream_chatters": int(row.chatters or 0),
        }
