"""Persistence and interval-based audience telemetry for viewing connections."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.models.streaming import StreamSession, StreamSessionStatus, StreamViewerSession
from repositories.base import BaseRepository


def _as_utc(value: datetime) -> datetime:
    # SQLite drops timezone information; persisted naive timestamps represent UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class StreamViewerSessionRepository(BaseRepository[StreamViewerSession]):
    """Audience metrics use persisted intervals, not live process state.

    Reporting periods must have start < end or raise ValueError. Callers supply
    the reporting end (for example, now); aggregation never reads a wall clock.
    Naive timestamps mean UTC, and aware timestamps are normalized to UTC.
    Missing/nonqualifying streams return zero; database errors propagate.
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, StreamViewerSession)

    async def _effective_intervals(
        self, stream_id: uuid.UUID, reporting_start: datetime, reporting_end: datetime
    ) -> list[tuple[uuid.UUID, datetime, datetime]]:
        start, end = _as_utc(reporting_start), _as_utc(reporting_end)
        if end <= start:
            raise ValueError("Reporting end must be after reporting start")
        result = await self.db.execute(
            select(
                StreamViewerSession.user_id,
                StreamViewerSession.joined_at,
                StreamViewerSession.lease_expires_at,
                StreamViewerSession.left_at,
                StreamSession.started_at,
                StreamSession.ended_at,
            )
            .join(StreamSession, StreamSession.id == StreamViewerSession.stream_session_id)
            .where(
                StreamSession.id == stream_id,
                StreamSession.status.in_(
                    (StreamSessionStatus.ACTIVE, StreamSessionStatus.ENDED)
                ),
                StreamSession.started_at < end,
                or_(StreamSession.ended_at.is_(None), StreamSession.ended_at > start),
                StreamViewerSession.joined_at < end,
                StreamViewerSession.lease_expires_at > start,
                or_(StreamViewerSession.left_at.is_(None), StreamViewerSession.left_at > start),
            )
        )
        intervals: list[tuple[uuid.UUID, datetime, datetime]] = []
        for user_id, joined_at, lease_expires_at, left_at, started_at, ended_at in result.all():
            interval_start = max(start, _as_utc(joined_at), _as_utc(started_at))
            ends = [end, _as_utc(lease_expires_at)]
            if left_at is not None:
                ends.append(_as_utc(left_at))
            if ended_at is not None:
                ends.append(_as_utc(ended_at))
            interval_end = min(ends)
            if interval_start < interval_end:
                intervals.append((user_id, interval_start, interval_end))
        return intervals

    @staticmethod
    def _union_per_user(
        intervals: list[tuple[uuid.UUID, datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        by_user: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {}
        for user_id, start, end in intervals:
            by_user.setdefault(user_id, []).append((start, end))
        merged: list[tuple[datetime, datetime]] = []
        for user_intervals in by_user.values():
            ordered = sorted(user_intervals)
            start, end = ordered[0]
            for next_start, next_end in ordered[1:]:
                if next_start <= end:
                    end = max(end, next_end)
                else:
                    merged.append((start, end))
                    start, end = next_start, next_end
            merged.append((start, end))
        return merged

    async def count_unique_viewers(
        self, stream_id: uuid.UUID, reporting_start: datetime, reporting_end: datetime
    ) -> int:
        """Count distinct users with positive viewing time in [start, end).

        Only ACTIVE/ENDED streams qualify. All timestamps are interpreted as UTC
        when naive; aware timestamps are converted to UTC.
        """
        intervals = await self._effective_intervals(stream_id, reporting_start, reporting_end)
        return len({user_id for user_id, _, _ in intervals})

    async def total_watch_duration(
        self, stream_id: uuid.UUID, reporting_start: datetime, reporting_end: datetime
    ) -> float:
        """Return audience-seconds in [start, end), unioning tabs/devices per user.

        Intervals are clipped to stream start/end, lease expiry, explicit leave,
        and the reporting period. Only ACTIVE/ENDED streams qualify.
        """
        intervals = await self._effective_intervals(stream_id, reporting_start, reporting_end)
        duration = sum(
            (end - start for start, end in self._union_per_user(intervals)),
            timedelta(),
        )
        return duration.total_seconds()

    async def count_current_viewers(self, stream_id: uuid.UUID, at: datetime) -> int:
        """Count distinct users present at `at` on an ACTIVE stream.

        Joins/stream starts are inclusive; leaves, leases, and stream ends are
        exclusive. This also supports supplied historical points on active streams.
        """
        at = _as_utc(at)
        result = await self.db.execute(
            select(func.count(func.distinct(StreamViewerSession.user_id)))
            .join(StreamSession, StreamSession.id == StreamViewerSession.stream_session_id)
            .where(
                StreamSession.id == stream_id,
                StreamSession.status == StreamSessionStatus.ACTIVE,
                StreamSession.started_at <= at,
                or_(StreamSession.ended_at.is_(None), StreamSession.ended_at > at),
                StreamViewerSession.joined_at <= at,
                StreamViewerSession.lease_expires_at > at,
                or_(StreamViewerSession.left_at.is_(None), StreamViewerSession.left_at > at),
            )
        )
        return int(result.scalar_one())

    async def peak_concurrent_viewers(
        self, stream_id: uuid.UUID, reporting_start: datetime, reporting_end: datetime
    ) -> int:
        """Return peak distinct-user concurrency in [start, end) on ACTIVE/ENDED streams."""
        intervals = await self._effective_intervals(stream_id, reporting_start, reporting_end)
        events = [
            event
            for start, end in self._union_per_user(intervals)
            for event in ((start, 1), (end, -1))
        ]
        current = peak = 0
        # Sorting -1 before +1 implements half-open intervals at tied timestamps.
        for _, delta in sorted(events):
            current += delta
            peak = max(peak, current)
        return peak

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
