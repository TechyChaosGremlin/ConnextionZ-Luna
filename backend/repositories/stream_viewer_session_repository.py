"""Transactional persistence for authenticated viewing connections."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.models.streaming import StreamSession, StreamViewerSession
from repositories.base import BaseRepository


class StreamViewerSessionRepository(BaseRepository[StreamViewerSession]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, StreamViewerSession)

    async def lock_stream(self, stream_id: uuid.UUID) -> StreamSession | None:
        """Lock the parent before viewer rows; termination uses the same lock order."""
        result = await self.db.execute(
            select(StreamSession)
            .options(lazyload(StreamSession.destinations))
            .where(StreamSession.id == stream_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_attempt(
        self, stream_id: uuid.UUID, user_id: uuid.UUID, client_session_id: uuid.UUID
    ) -> StreamViewerSession | None:
        result = await self.db.execute(
            select(StreamViewerSession).where(
                StreamViewerSession.stream_session_id == stream_id,
                StreamViewerSession.user_id == user_id,
                StreamViewerSession.client_session_id == client_session_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_attempt(
        self,
        *,
        stream_id: uuid.UUID,
        user_id: uuid.UUID,
        client_session_id: uuid.UUID,
        joined_at: datetime,
        lease_expires_at: datetime,
    ) -> StreamViewerSession:
        dialect = self.db.get_bind().dialect.name
        if dialect == "postgresql":
            statement = pg_insert(StreamViewerSession)
        elif dialect == "sqlite":
            statement = sqlite_insert(StreamViewerSession)
        else:
            raise NotImplementedError(f"Viewer sessions do not support database dialect {dialect}")
        result = await self.db.execute(
            statement.values(
                stream_session_id=stream_id,
                user_id=user_id,
                client_session_id=client_session_id,
                joined_at=joined_at,
                lease_expires_at=lease_expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=["stream_session_id", "user_id", "client_session_id"]
            )
            .returning(StreamViewerSession)
        )
        viewer = result.scalar_one_or_none()
        if viewer is None:
            viewer = await self.get_attempt(stream_id, user_id, client_session_id)
        if viewer is None:
            raise RuntimeError("Viewer join conflict did not resolve to a persisted attempt")
        return viewer

    async def lock_owned(
        self, stream_id: uuid.UUID, viewer_session_id: uuid.UUID, user_id: uuid.UUID
    ) -> StreamViewerSession | None:
        result = await self.db.execute(
            select(StreamViewerSession)
            .where(
                StreamViewerSession.id == viewer_session_id,
                StreamViewerSession.stream_session_id == stream_id,
                StreamViewerSession.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def renew(
        self, viewer: StreamViewerSession, now: datetime, deadline: datetime
    ) -> StreamViewerSession | None:
        result = await self.db.execute(
            update(StreamViewerSession)
            .where(
                StreamViewerSession.id == viewer.id,
                StreamViewerSession.stream_session_id == viewer.stream_session_id,
                StreamViewerSession.user_id == viewer.user_id,
                StreamViewerSession.left_at.is_(None),
                StreamViewerSession.joined_at <= now,
                StreamViewerSession.lease_expires_at > now,
            )
            .values(
                lease_expires_at=case(
                    (StreamViewerSession.lease_expires_at < deadline, deadline),
                    else_=StreamViewerSession.lease_expires_at,
                )
            )
            .returning(StreamViewerSession)
            .execution_options(populate_existing=True, synchronize_session="fetch")
        )
        return result.scalar_one_or_none()

    async def finalize(
        self, viewer: StreamViewerSession, left_at: datetime
    ) -> StreamViewerSession:
        result = await self.db.execute(
            update(StreamViewerSession)
            .where(
                StreamViewerSession.id == viewer.id,
                StreamViewerSession.stream_session_id == viewer.stream_session_id,
                StreamViewerSession.user_id == viewer.user_id,
                StreamViewerSession.left_at.is_(None),
            )
            .values(left_at=left_at)
            .returning(StreamViewerSession)
            .execution_options(populate_existing=True, synchronize_session="fetch")
        )
        finalized = result.scalar_one_or_none()
        if finalized is None:
            finalized = await self.lock_owned(
                viewer.stream_session_id, viewer.id, viewer.user_id
            )
        if finalized is None:
            raise RuntimeError("Viewer session disappeared during finalization")
        return finalized
