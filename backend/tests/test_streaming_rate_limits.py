"""Exercise the active streaming routes/service with real session persistence."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import Column, Index, MetaData, Table, Uuid, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.dependencies import get_db_session
from app.main import create_app
from app.models.streaming import (
    LIVE_STREAM_OWNER_INDEX,
    StreamDestination,
    StreamDestinationStatus,
    StreamSession,
    StreamSessionStatus,
)
from app.models.user import User
from app.rate_limits import (
    RATE_LIMIT_MESSAGE,
    STREAM_ACTION_LIMITS,
    ActionRateLimiter,
)
from features.auth.middleware import get_current_active_user
from features.streaming.ffmpeg import FFmpegError
import features.streaming.service as streaming_service
from repositories.stream_session_repository import StreamSessionRepository

USER_ONE = uuid.UUID(int=1)
USER_TWO = uuid.UUID(int=2)
BODY = {"input_source": "input.mp4", "platforms": ["twitch", "youtube"]}


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'streams.db'}")
    metadata = MetaData()
    Table("users", metadata, Column("id", Uuid(native_uuid=False), primary_key=True))
    Table(
        "connected_stream_accounts",
        metadata,
        Column("id", Uuid(native_uuid=False), primary_key=True),
    )
    StreamSession.__table__.to_metadata(metadata)
    StreamDestination.__table__.to_metadata(metadata)
    # SQLite gives a native UUID column numeric affinity; use text UUID storage.
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PG_UUID):
                column.type = Uuid(native_uuid=False)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            metadata.tables["users"].insert(), [{"id": USER_ONE}, {"id": USER_TWO}]
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = [100.0]
    monkeypatch.setattr(
        streaming_service,
        "stream_action_limiter",
        ActionRateLimiter(STREAM_ACTION_LIMITS, clock=lambda: now[0]),
    )
    manager = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(return_value=True),
        status=AsyncMock(return_value=SimpleNamespace(running=True)),
    )
    monkeypatch.setattr(streaming_service.stream_manager, "start", manager.start)
    monkeypatch.setattr(streaming_service.stream_manager, "stop", manager.stop)
    monkeypatch.setattr(streaming_service.stream_manager, "status", manager.status)
    monkeypatch.setattr(streaming_service, "async_session_factory", sessions)
    users = {
        "user-one": User(id=USER_ONE),
        "user-one-new-token": User(id=USER_ONE),
        "user-two": User(id=USER_TWO),
    }

    async def current_user(request: Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in users:
            raise HTTPException(status_code=401, detail="Authentication required")
        return users[token]

    async def get_db():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_current_active_user] = current_user
    app.dependency_overrides[get_db_session] = get_db
    try:
        yield SimpleNamespace(app=app, engine=engine, sessions=sessions, manager=manager, now=now)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.10", 1234)),
        base_url="http://test",
    ) as client:
        yield client


async def start(client, token="user-one"):
    return await client.post(
        "/api/streams", json=BODY, headers={"Authorization": f"Bearer {token}"}
    )


async def stop(client, stream_id, token="user-one"):
    return await client.post(
        f"/api/streams/{stream_id}/stop", headers={"Authorization": f"Bearer {token}"}
    )


def assert_limited(response, retry_after=60):
    assert response.status_code == 429
    assert response.json() == {
        "error": {"code": "TOO_MANY_REQUESTS", "message": RATE_LIMIT_MESSAGE}
    }
    assert int(response.headers["Retry-After"]) == retry_after


@pytest.mark.asyncio
async def test_start_within_allowed_rate_succeeds(client, harness):
    response = await start(client)
    assert response.status_code == 201
    assert response.json()["status"] == "active"
    assert response.json()["platforms"] == BODY["platforms"]
    harness.manager.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_starts_eventually_return_429(client, harness):
    assert (await start(client)).status_code == 201
    assert_limited(await start(client))  # Occupied slot still consumes an allowed attempt.
    assert_limited(await start(client))  # The minute action budget is now exhausted.
    assert harness.manager.start.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_limit_prevents_second_active_stream(client, harness):
    assert (await start(client)).status_code == 201
    harness.now[0] += 60  # Expiring the frequency window cannot release the live slot.
    assert_limited(await start(client))
    assert harness.manager.start.await_count == 1
    async with harness.sessions() as db:
        streams = (await db.scalars(select(StreamSession))).all()
        assert len(streams) == 1
        assert streams[0].status == StreamSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_stopping_releases_concurrent_slot(client, harness):
    first = await start(client)
    assert (await stop(client, first.json()["stream_id"])).status_code == 200
    second = await start(client)
    assert second.status_code == 201
    assert second.json()["stream_id"] != first.json()["stream_id"]
    assert harness.manager.start.await_count == 2


@pytest.mark.asyncio
async def test_rapid_stop_start_churn_is_limited(client, harness):
    for _ in range(2):
        response = await start(client)
        assert response.status_code == 201
        assert (await stop(client, response.json()["stream_id"])).status_code == 200
    assert_limited(await start(client))
    assert harness.manager.start.await_count == 2
    assert harness.manager.stop.await_count == 2
    harness.now[0] += 59.5
    assert_limited(await start(client), retry_after=1)
    harness.now[0] += 0.5
    assert (await start(client)).status_code == 201


@pytest.mark.asyncio
async def test_hourly_churn_budget_survives_minute_windows(client, harness):
    for _ in range(5):
        for _ in range(2):
            response = await start(client)
            assert response.status_code == 201
            assert (await stop(client, response.json()["stream_id"])).status_code == 200
        harness.now[0] += 60
    assert_limited(await start(client), retry_after=3300)
    assert harness.manager.start.await_count == 10
    harness.now[0] = 3700
    assert (await start(client)).status_code == 201


@pytest.mark.asyncio
async def test_creators_have_independent_concurrent_limits(client, harness):
    assert (await start(client)).status_code == 201
    assert (await start(client, "user-two")).status_code == 201
    assert_limited(await start(client))
    assert_limited(await start(client, "user-two"))
    assert harness.manager.start.await_count == 2


@pytest.mark.asyncio
async def test_normal_start_stop_status_and_idempotence_unchanged(client, harness):
    response = await start(client)
    stream_id = response.json()["stream_id"]
    headers = {"Authorization": "Bearer user-one"}
    status = await client.get(f"/api/streams/{stream_id}", headers=headers)
    assert status.status_code == 200
    assert status.json()["process_active"] is True
    assert status.json()["status"] == "active"
    listed = await client.get("/api/streams", headers=headers)
    assert listed.status_code == 200
    expected = response.json()
    expected["started_at"] = expected["started_at"].removesuffix("Z")
    assert listed.json() == [expected]  # SQLite does not retain timezone offsets.
    stopped = await stop(client, stream_id)
    assert stopped.status_code == 200
    assert stopped.json() == {"stream_id": stream_id, "status": "ended"}
    assert (await stop(client, stream_id)).json() == stopped.json()
    harness.manager.stop.assert_awaited_once()
    async with harness.sessions() as db:
        stream = await db.get(StreamSession, uuid.UUID(stream_id))
        assert stream.ended_at is not None
        assert all(d.status == StreamDestinationStatus.ENDED for d in stream.destinations)


@pytest.mark.asyncio
async def test_global_60_requests_per_ip_still_applies(client, harness):
    headers = {"Authorization": "Bearer user-one"}
    for _ in range(59):
        assert (await client.get("/api/streams", headers=headers)).status_code == 200
    response = await start(client)
    assert response.status_code == 201
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert_limited(await stop(client, response.json()["stream_id"]))
    harness.manager.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_frequency_limit_prevents_repeated_work(client, harness):
    response = await start(client)
    stream_id = response.json()["stream_id"]
    for _ in range(10):
        assert (await stop(client, stream_id)).status_code == 200
    assert_limited(await stop(client, stream_id))
    harness.manager.stop.assert_awaited_once()
    harness.now[0] += 60
    assert (await stop(client, stream_id)).status_code == 200


@pytest.mark.asyncio
async def test_authentication_and_ownership_responses_are_preserved(client, harness):
    assert (await start(client, "invalid")).status_code == 401
    response = await start(client)
    stream_id = response.json()["stream_id"]
    for _ in range(11):
        assert (await stop(client, stream_id, "user-two")).status_code == 404
    assert (await stop(client, uuid.uuid4())).status_code == 404
    assert (await stop(client, stream_id, "invalid")).status_code == 401
    assert (await stop(client, stream_id)).status_code == 200
    harness.manager.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_user_cannot_bypass_limits_with_new_token_or_ip(client, harness):
    response = await start(client)
    assert (await stop(client, response.json()["stream_id"])).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.11", 1234)),
        base_url="http://test",
    ) as other_ip:
        response = await start(other_ip, "user-one-new-token")
        assert response.status_code == 201
        assert (await stop(other_ip, response.json()["stream_id"])).status_code == 200
        assert_limited(await start(other_ip, "user-one-new-token"))
    assert harness.manager.start.await_count == 2


@pytest.mark.asyncio
async def test_simultaneous_starts_reserve_slot_before_process_launch(client, harness, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    second_reserving = asyncio.Event()
    reserve = StreamSessionRepository.reserve_live_slot
    reservations = 0

    async def observed_reservation(repository, stream):
        nonlocal reservations
        reservations += 1
        if reservations == 2:
            second_reserving.set()
        await reserve(repository, stream)

    async def paused_start(**kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(StreamSessionRepository, "reserve_live_slot", observed_reservation)
    harness.manager.start.side_effect = paused_start
    first = asyncio.create_task(start(client))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(start(client))
        await asyncio.wait_for(second_reserving.wait(), timeout=5)
        assert harness.manager.start.await_count == 1
    finally:
        release.set()
        responses = await asyncio.wait_for(
            asyncio.gather(first, *([second] if second is not None else [])), timeout=5
        )
    assert sorted(response.status_code for response in responses) == [201, 429]
    assert_limited(next(response for response in responses if response.status_code == 429))
    assert harness.manager.start.await_count == 1


@pytest.mark.asyncio
async def test_failed_start_releases_slot_but_consumes_frequency_budget(client, harness):
    harness.manager.start.side_effect = FFmpegError("Test startup failure")
    assert (await start(client)).status_code == 503
    harness.manager.start.side_effect = None
    response = await start(client)
    assert response.status_code == 201
    assert (await stop(client, response.json()["stream_id"])).status_code == 200
    assert_limited(await start(client))
    async with harness.sessions() as db:
        streams = (await db.scalars(select(StreamSession))).all()
        assert {stream.status for stream in streams} == {
            StreamSessionStatus.FAILED,
            StreamSessionStatus.ENDED,
        }


@pytest.mark.asyncio
async def test_unexpected_process_exit_releases_slot(client, harness):
    response = await start(client)
    await streaming_service._persist_process_exit(
        uuid.UUID(response.json()["stream_id"]), return_code=1, stop_requested=False
    )
    assert (await start(client)).status_code == 201


@pytest.mark.asyncio
async def test_persisted_pending_session_occupies_slot(client, harness):
    async with harness.sessions() as db:
        db.add(StreamSession(owner_id=USER_ONE, input_source="pending.mp4"))
        await db.commit()
    assert_limited(await start(client))
    harness.manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_migration_installs_same_database_guard(harness, monkeypatch):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "199_stream_live_owner_limit.py"
    )
    spec = importlib.util.spec_from_file_location("stream_live_owner_migration", path)
    assert spec is not None and spec.loader is not None

    def migrate(connection):
        table = Table("stream_sessions", MetaData(), autoload_with=connection)

        def create_index(name, table_name, columns, **kwargs):
            assert table_name == table.name
            Index(name, *(table.c[column] for column in columns), **kwargs).create(connection)

        def drop_index(name, table_name):
            assert table_name == table.name
            next(index for index in table.indexes if index.name == name).drop(connection)

        # Execute the revision's DDL without the repository's local alembic
        # package shadowing the installed CLI package during pytest collection.
        monkeypatch.setitem(
            sys.modules,
            "alembic",
            SimpleNamespace(op=SimpleNamespace(create_index=create_index, drop_index=drop_index)),
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.downgrade()
        migration.upgrade()

    async with harness.engine.begin() as connection:
        await connection.run_sync(migrate)
    async with harness.sessions() as db:
        db.add(StreamSession(owner_id=USER_ONE, input_source="pending.mp4"))
        await db.commit()
    # The migrated index, not just ORM metadata, enforces the active route's cap.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app), base_url="http://test"
    ) as client:
        assert_limited(await start(client))


def test_live_owner_index_compiles_for_postgres():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    index = next(
        index for index in StreamSession.__table__.indexes if index.name == LIVE_STREAM_OWNER_INDEX
    )
    assert str(CreateIndex(index).compile(dialect=postgresql.dialect())) == (
        "CREATE UNIQUE INDEX uq_stream_sessions_live_owner ON stream_sessions "
        "(owner_id) WHERE status IN ('pending', 'active')"
    )
