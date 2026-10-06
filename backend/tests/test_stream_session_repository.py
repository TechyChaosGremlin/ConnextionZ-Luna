"""Queries for persisted streaming sessions."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, create_engine
from sqlalchemy.exc import IntegrityError

from app.models.streaming import (
    LIVE_STREAM_OWNER_INDEX,
    StreamPlatform,
    StreamSession,
    StreamSessionStatus,
)
from app.rate_limits import ActionRateLimitExceeded
from features.streaming.service import StreamingService
from repositories.stream_session_repository import StreamSessionRepository
from services.creator_analytics_service import CreatorAnalyticsService


@pytest.mark.asyncio
async def test_ended_stream_scope_and_duration_against_persisted_rows() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    streams = Table(
        "stream_sessions", metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_id", Uuid, nullable=False),
        Column("status", String, nullable=False),
        Column("started_at", DateTime),
        Column("ended_at", DateTime),
    )
    metadata.create_all(engine)
    owner_id, other_owner_id = uuid.uuid4(), uuid.uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    records = [
        (owner_id, "ended", start - timedelta(hours=1), start),
        (owner_id, "ended", end - timedelta(hours=2), end),
        (owner_id, "ended", start, start - timedelta(microseconds=1)),
        (owner_id, "ended", end, end + timedelta(microseconds=1)),
        (owner_id, "active", start, start + timedelta(hours=1)),
        (owner_id, "pending", start, start + timedelta(hours=1)),
        (owner_id, "failed", start, start + timedelta(hours=1)),
        (other_owner_id, "ended", start, start + timedelta(hours=1)),
        (owner_id, "ended", None, start),
        (owner_id, "ended", start, None),
    ]
    rows = [
        {
            "id": uuid.uuid4(), "owner_id": creator_id, "status": status,
            "started_at": started_at, "ended_at": ended_at,
        }
        for creator_id, status, started_at, ended_at in records
    ]
    by_id = {row["id"]: SimpleNamespace(**row) for row in rows}
    try:
        with engine.connect() as connection:
            connection.execute(streams.insert(), rows)

            async def execute(statement):
                # Execute the repository's real predicates without loading ORM relationships.
                ids = connection.execute(statement.with_only_columns(StreamSession.id)).scalars()
                selected = [by_id[stream_id] for stream_id in ids]
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: selected)
                )

            db = SimpleNamespace(execute=AsyncMock(side_effect=execute))
            service = CreatorAnalyticsService(db)
            assert await service._stream_session_totals(owner_id, start, end) == (2, 10800.0)
            assert await service._total_broadcast_duration(owner_id, start, end) == 10800.0
            assert await service._stream_session_totals(uuid.uuid4(), start, end) == (0, 0.0)
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platforms_by_session, expected",
    [
        ([], []),
        ([[]], []),
        ([["twitch"]], [("twitch", 1)]),
        ([["twitch"], ["twitch"]], [("twitch", 2)]),
        (
            [["youtube", "twitch", "twitch"], ["twitch"], ["kick"], ["facebook"]],
            [("facebook", 1), ("kick", 1), ("twitch", 2), ("youtube", 1)],
        ),
    ],
)
async def test_ended_destination_breakdown_from_persisted_rows(
    platforms_by_session, expected
) -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    streams = Table(
        "stream_sessions", metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_id", Uuid, nullable=False),
        Column("status", String, nullable=False),
        Column("started_at", DateTime),
        Column("ended_at", DateTime),
    )
    destinations = Table(
        "stream_destinations", metadata,
        Column("id", Uuid, primary_key=True),
        Column("stream_session_id", Uuid, nullable=False),
        Column("platform", String, nullable=False),
        Column("status", String, nullable=False),
    )
    metadata.create_all(engine)
    owner_id = uuid.uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    session_rows = []
    destination_rows = []

    def add_session(creator_id, status, started_at, ended_at, platforms):
        stream_id = uuid.uuid4()
        session_rows.append({
            "id": stream_id, "owner_id": creator_id, "status": status,
            "started_at": started_at, "ended_at": ended_at,
        })
        for index, platform in enumerate(platforms):
            destination_rows.append({
                "id": uuid.uuid4(), "stream_session_id": stream_id, "platform": platform,
                "status": "failed" if index % 2 else "ended",
            })

    for index, platforms in enumerate(platforms_by_session):
        ended_at = start if index == 0 else end
        add_session(owner_id, "ended", ended_at - timedelta(hours=1), ended_at, platforms)
    for status in ("pending", "active", "failed"):
        add_session(owner_id, status, start, end, ["youtube"])
    add_session(uuid.uuid4(), "ended", start, end, ["youtube"])
    add_session(owner_id, "ended", start - timedelta(hours=1),
                start - timedelta(microseconds=1), ["youtube"])
    add_session(owner_id, "ended", end, end + timedelta(microseconds=1), ["youtube"])
    add_session(owner_id, "ended", None, end, ["youtube"])
    add_session(owner_id, "ended", start, None, ["youtube"])
    add_session(owner_id, "ended", end + timedelta(seconds=1), end, ["youtube"])

    try:
        with engine.connect() as connection:
            connection.execute(streams.insert(), session_rows)
            connection.execute(destinations.insert(), destination_rows)

            async def execute(statement):
                return connection.execute(statement)

            repository = StreamSessionRepository(
                SimpleNamespace(execute=AsyncMock(side_effect=execute))
            )
            result = await repository.ended_destination_counts_for_owner_in_period(
                owner_id, start, end
            )

            assert [(row["platform"].value, row["ended_sessions"]) for row in result] == expected
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_get_active_filters_and_limits_sessions() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.scalars.return_value.all.return_value = []

    assert await StreamSessionRepository(db).get_active(limit=7) == []

    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "stream_sessions.status = 'active'" in sql
    assert "ORDER BY stream_sessions.started_at DESC" in sql
    assert "LIMIT 7" in sql


@pytest.mark.asyncio
async def test_get_for_owner_only_returns_owned_sessions() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.scalars.return_value.all.return_value = []
    owner_id = uuid.uuid4()

    assert await StreamSessionRepository(db).get_for_owner(owner_id) == []

    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert f"stream_sessions.owner_id = '{owner_id.hex}'" in sql
    assert "ORDER BY stream_sessions.created_at DESC" in sql


@pytest.mark.asyncio
async def test_service_list_preserves_owner_stream_response() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    owner_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    stream = SimpleNamespace(
        id=stream_id,
        status=StreamSessionStatus.ACTIVE,
        destinations=[SimpleNamespace(platform=StreamPlatform.TWITCH)],
        created_at=created_at,
        started_at=created_at,
        ended_at=None,
    )
    db.execute.return_value.scalars.return_value.all.return_value = [stream]

    responses = await StreamingService(db).list(SimpleNamespace(id=owner_id))

    assert len(responses) == 1
    assert responses[0].stream_id == stream_id
    assert responses[0].platforms == [StreamPlatform.TWITCH]
    statement = db.execute.call_args.args[0]
    assert f"stream_sessions.owner_id = '{owner_id.hex}'" in str(
        statement.compile(compile_kwargs={"literal_binds": True})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", ["psycopg", "asyncpg", "sqlite"])
async def test_live_slot_violation_rolls_back_and_maps_to_rate_limit(driver: str) -> None:
    class ConstraintViolation(Exception):
        def __init__(self) -> None:
            super().__init__("Unique constraint violation")
            self.constraint_name = LIVE_STREAM_OWNER_INDEX
            self.diag = SimpleNamespace(constraint_name=LIVE_STREAM_OWNER_INDEX)

    original = Exception("Unique constraint violation")
    if driver == "psycopg":
        original = ConstraintViolation()
    elif driver == "asyncpg":
        original.__cause__ = ConstraintViolation()
    else:
        original = Exception("UNIQUE constraint failed: stream_sessions.owner_id")
    db = MagicMock()
    db.flush = AsyncMock(side_effect=IntegrityError("INSERT", {}, original))
    db.rollback = AsyncMock()
    stream = StreamSession(owner_id=uuid.uuid4(), input_source="input.mp4")

    with pytest.raises(ActionRateLimitExceeded) as failure:
        await StreamSessionRepository(db).reserve_live_slot(stream)

    assert failure.value.retry_after == 60
    db.add.assert_called_once_with(stream)
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_slot_reservation_does_not_mask_other_database_errors() -> None:
    error = IntegrityError("INSERT", {}, Exception("Foreign key constraint violation"))
    db = MagicMock()
    db.flush = AsyncMock(side_effect=error)
    db.rollback = AsyncMock()

    with pytest.raises(IntegrityError) as failure:
        await StreamSessionRepository(db).reserve_live_slot(
            StreamSession(owner_id=uuid.uuid4(), input_source="input.mp4")
        )

    assert failure.value is error
    db.rollback.assert_awaited_once()