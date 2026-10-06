"""Authorization for social actions explicitly originating in a Luna stream."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.streaming import StreamSession, StreamSessionStatus, StreamViewerSession
from repositories.stream_viewer_session_repository import StreamViewerSessionRepository


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


class StreamEngagementRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def authorize_follow(
        self,
        *,
        stream_id: uuid.UUID,
        creator_id: uuid.UUID,
        user_id: uuid.UUID,
        at: datetime | None = None,
    ) -> StreamSession:
        # Termination and viewer updates take the same parent lock first.
        stream = await StreamViewerSessionRepository(self.db).lock_stream(stream_id)
        if stream is None:
            raise NotFoundError("Stream not found")
        at = datetime.now(timezone.utc) if at is None else _as_utc(at)
        if stream.owner_id != creator_id:
            raise ForbiddenError("Stream is not owned by the followed creator")
        if (
            stream.status != StreamSessionStatus.ACTIVE
            or stream.started_at is None
            or _as_utc(stream.started_at) > at
            or stream.ended_at is not None
        ):
            raise ConflictError("Stream is not active")
        result = await self.db.execute(
            select(StreamViewerSession.id)
            .where(
                StreamViewerSession.stream_session_id == stream_id,
                StreamViewerSession.user_id == user_id,
                StreamViewerSession.joined_at <= at,
                StreamViewerSession.lease_expires_at > at,
                StreamViewerSession.left_at.is_(None),
            )
            .limit(1)
        )
        if result.scalar_one_or_none() is None:
            raise ForbiddenError("Join the active stream before attributing a follow")
        return stream
