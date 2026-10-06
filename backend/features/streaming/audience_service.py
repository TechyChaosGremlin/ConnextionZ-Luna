"""Authenticated, lease-backed viewer presence without audience aggregation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import ConflictError, NotFoundError
from app.models.streaming import StreamSession, StreamSessionStatus, StreamViewerSession
from app.models.user import User
from app.rate_limits import (
    STREAM_VIEWER_ACTION_LIMITS,
    STREAM_VIEWER_HEARTBEAT_ACTION,
    STREAM_VIEWER_JOIN_ACTION,
    STREAM_VIEWER_LEAVE_ACTION,
    ActionRateLimiter,
    client_identity,
)
from features.streaming.schemas import ViewerSessionResponse
from repositories.stream_viewer_session_repository import StreamViewerSessionRepository


viewer_action_limiter = ActionRateLimiter(STREAM_VIEWER_ACTION_LIMITS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    # SQLite test persistence drops timezone information; all stored times are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class AudienceService:
    def __init__(self, db: AsyncSession) -> None:
        self.repository = StreamViewerSessionRepository(db)

    async def _stream(self, stream_id: uuid.UUID) -> StreamSession:
        stream = await self.repository.lock_stream(stream_id)
        if stream is None:
            raise NotFoundError("Stream not found")
        return stream

    async def _viewer(
        self, stream_id: uuid.UUID, viewer_session_id: uuid.UUID, user_id: uuid.UUID
    ) -> StreamViewerSession:
        viewer = await self.repository.lock_owned(stream_id, viewer_session_id, user_id)
        if viewer is None:
            raise NotFoundError("Viewer session not found")
        return viewer

    @staticmethod
    def _require_active_stream(stream: StreamSession, now: datetime) -> None:
        if (
            stream.status != StreamSessionStatus.ACTIVE
            or stream.started_at is None
            or _as_utc(stream.started_at) > now
            or stream.ended_at is not None
        ):
            raise ConflictError("Stream is not active")

    @staticmethod
    def _response(
        viewer: StreamViewerSession, stream: StreamSession, now: datetime
    ) -> ViewerSessionResponse:
        return ViewerSessionResponse(
            viewer_session_id=viewer.id,
            stream_id=viewer.stream_session_id,
            client_session_id=viewer.client_session_id,
            joined_at=_as_utc(viewer.joined_at),
            lease_expires_at=_as_utc(viewer.lease_expires_at),
            left_at=_as_utc(viewer.left_at) if viewer.left_at is not None else None,
            is_active=(
                viewer.left_at is None
                and _as_utc(viewer.joined_at) <= now
                and _as_utc(viewer.lease_expires_at) > now
                and stream.status == StreamSessionStatus.ACTIVE
            ),
        )

    async def join(
        self, stream_id: uuid.UUID, client_session_id: uuid.UUID, viewer_user: User
    ) -> ViewerSessionResponse:
        viewer_action_limiter.consume(
            client_identity(viewer_user.id, ""), {STREAM_VIEWER_JOIN_ACTION: 1}
        )
        stream = await self._stream(stream_id)
        now = _utcnow()
        self._require_active_stream(stream, now)
        viewer = await self.repository.get_attempt(stream_id, viewer_user.id, client_session_id)
        if viewer is None:
            viewer = await self.repository.create_attempt(
                stream_id=stream_id,
                user_id=viewer_user.id,
                client_session_id=client_session_id,
                joined_at=now,
                lease_expires_at=now + timedelta(seconds=settings.streaming_viewer_lease_seconds),
            )
        return self._response(viewer, stream, now)

    async def heartbeat(
        self, stream_id: uuid.UUID, viewer_session_id: uuid.UUID, viewer_user: User
    ) -> ViewerSessionResponse:
        viewer_action_limiter.consume(
            client_identity(viewer_user.id, ""), {STREAM_VIEWER_HEARTBEAT_ACTION: 1}
        )
        stream = await self._stream(stream_id)
        viewer = await self._viewer(stream_id, viewer_session_id, viewer_user.id)
        now = _utcnow()
        self._require_active_stream(stream, now)
        renewed = await self.repository.renew(
            viewer, now, now + timedelta(seconds=settings.streaming_viewer_lease_seconds)
        )
        if renewed is None:
            raise ConflictError("Viewer session is closed, expired, or has not started")
        return self._response(renewed, stream, now)

    async def leave(
        self, stream_id: uuid.UUID, viewer_session_id: uuid.UUID, viewer_user: User
    ) -> ViewerSessionResponse:
        viewer_action_limiter.consume(
            client_identity(viewer_user.id, ""), {STREAM_VIEWER_LEAVE_ACTION: 1}
        )
        stream = await self._stream(stream_id)
        viewer = await self._viewer(stream_id, viewer_session_id, viewer_user.id)
        now = _utcnow()
        if viewer.left_at is None:
            boundaries = [now, _as_utc(viewer.lease_expires_at)]
            if stream.ended_at is not None:
                boundaries.append(_as_utc(stream.ended_at))
            left_at = min(boundaries)
            if left_at < _as_utc(viewer.joined_at):
                raise ConflictError("Viewer session end precedes its join time")
            viewer = await self.repository.finalize(viewer, left_at)
        return self._response(viewer, stream, now)
