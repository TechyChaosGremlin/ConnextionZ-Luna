"""Authenticated viewer lifecycle and aggregation against persisted streaming rows."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, Table, Uuid, event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.dependencies import get_db_session
from app.main import create_app
from app.models.streaming import (
    StreamDestination,
    StreamSession,
    StreamSessionStatus,
    StreamViewerSession,
)
from app.models.user import User
from app.rate_limits import (
    RATE_LIMIT_MESSAGE,
    STREAM_VIEWER_ACTION_LIMITS,
    STREAM_VIEWER_HEARTBEAT_ACTION,
    STREAM_VIEWER_JOIN_ACTION,
    STREAM_VIEWER_LEAVE_ACTION,
    ActionRateLimiter,
)
from features.auth.middleware import get_current_active_user
from features.streaming.audience_service import AudienceService
import features.streaming.audience_service as audience_service
import features.streaming.service as streaming_service
from repositories.stream_viewer_session_repository import StreamViewerSessionRepository


CREATOR, VIEWER_A, VIEWER_B = (uuid.UUID(int=value) for value in (1, 2, 3))
STREAM_ID, OTHER_STREAM_ID = uuid.UUID(int=10), uuid.UUID(int=11)
NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audience.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata = MetaData()
    Table("users", metadata, Column("id", Uuid(native_uuid=False), primary_key=True))
    Table(
        "connected_stream_accounts", metadata,
        Column("id", Uuid(native_uuid=False), primary_key=True),
    )
    for model in (StreamSession, StreamDestination, StreamViewerSession):
        model.__table__.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PG_UUID):
                column.type = Uuid(native_uuid=False)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            metadata.tables["users"].insert(),
            [{"id": identity} for identity in (CREATOR, VIEWER_A, VIEWER_B)],
        )
        await connection.execute(
            metadata.tables["stream_sessions"].insert(),
            [
                {
                    "id": stream_id, "owner_id": owner_id, "input_source": "input.mp4",
                    "status": StreamSessionStatus.ACTIVE,
                    "started_at": NOW - timedelta(minutes=1),
                }
                for stream_id, owner_id in ((STREAM_ID, CREATOR), (OTHER_STREAM_ID, VIEWER_B))
            ],
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = [NOW]
    rate_clock = [100.0]
    monkeypatch.setattr(audience_service, "_utcnow", lambda: now[0])
    monkeypatch.setattr(audience_service.settings, "streaming_viewer_lease_seconds", 60)
    monkeypatch.setattr(
        audience_service, "viewer_action_limiter",
        ActionRateLimiter(STREAM_VIEWER_ACTION_LIMITS, clock=lambda: rate_clock[0]),
    )
    users = {
        "viewer-a": User(id=VIEWER_A),
        "viewer-a-new-token": User(id=VIEWER_A),
        "viewer-b": User(id=VIEWER_B),
        "creator": User(id=CREATOR),
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
        yield SimpleNamespace(
            app=app, engine=engine, sessions=sessions, metadata=metadata,
            now=now, rate_clock=rate_clock, users=users,
        )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.40", 1234)),
        base_url="http://test",
    ) as client:
        yield client


def headers(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


async def join(client, client_id=None, token="viewer-a", stream_id=STREAM_ID, body=None):
    return await client.post(
        f"/api/streams/{stream_id}/viewers/join",
        json=body if body is not None else {"client_session_id": str(client_id or uuid.uuid4())},
        headers=headers(token),
    )


async def viewer_action(
    client, session_id, action, token="viewer-a", stream_id=STREAM_ID, body=None
):
    kwargs = {} if body is None else {"json": body}
    return await client.post(
        f"/api/streams/{stream_id}/viewers/{session_id}/{action}",
        headers=headers(token), **kwargs,
    )


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def stored_viewers(harness):
    async with harness.sessions() as db:
        return list((await db.scalars(select(StreamViewerSession))).all())


async def change_stream(harness, stream_id=STREAM_ID, **values):
    async with harness.sessions() as db:
        await db.execute(update(StreamSession).where(StreamSession.id == stream_id).values(**values))
        await db.commit()


def assert_limited(response, retry_after=60):
    assert response.status_code == 429
    assert response.json() == {
        "error": {"code": "TOO_MANY_REQUESTS", "message": RATE_LIMIT_MESSAGE}
    }
    assert int(response.headers["Retry-After"]) == retry_after


@pytest.mark.asyncio
async def test_join_uses_authenticated_viewer_not_creator_and_server_lease(client, harness):
    client_id = uuid.uuid4()
    response = await join(client, client_id)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "viewer_session_id", "stream_id", "client_session_id",
        "joined_at", "lease_expires_at", "left_at", "is_active",
    }
    assert uuid.UUID(data["viewer_session_id"]).version == 7
    assert data["stream_id"] == str(STREAM_ID)
    assert data["client_session_id"] == str(client_id)
    assert timestamp(data["joined_at"]) == NOW
    assert timestamp(data["lease_expires_at"]) == NOW + timedelta(seconds=60)
    assert data["left_at"] is None
    assert data["is_active"] is True
    viewer, = await stored_viewers(harness)
    assert viewer.user_id == VIEWER_A
    assert viewer.user_id != CREATOR


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "invalid"])
@pytest.mark.parametrize("action", ["join", "heartbeat", "leave"])
async def test_all_presence_operations_require_authentication(client, harness, token, action):
    response = (
        await join(client, token=token)
        if action == "join"
        else await viewer_action(client, uuid.uuid4(), action, token=token)
    )
    assert response.status_code == 401
    assert await stored_viewers(harness) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [StreamSessionStatus.PENDING, StreamSessionStatus.ENDED, StreamSessionStatus.FAILED]
)
async def test_join_rejects_inactive_parent(client, harness, status):
    await change_stream(harness, status=status)
    response = await join(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert await stored_viewers(harness) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"started_at": None},
        {"started_at": NOW + timedelta(seconds=1)},
        {"ended_at": NOW},
    ],
)
async def test_join_rejects_invalid_active_parent_timestamps(client, harness, values):
    await change_stream(harness, **values)
    assert (await join(client)).status_code == 409


@pytest.mark.asyncio
async def test_missing_stream_is_not_found(client, harness):
    assert (await join(client, stream_id=uuid.uuid4())).status_code == 404
    assert await stored_viewers(harness) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"client_session_id": None}, {"client_session_id": ""},
     {"client_session_id": "invalid"}, {"client_session_id": 1}],
)
async def test_client_attempt_key_is_required_and_must_be_uuid(client, harness, body):
    assert (await join(client, body=body)).status_code == 422
    assert await stored_viewers(harness) == []


@pytest.mark.asyncio
async def test_join_retry_does_not_extend_or_reset_interval(client, harness):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    harness.now[0] += timedelta(seconds=20)
    retry = await join(client, client_id)
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert len(await stored_viewers(harness)) == 1


@pytest.mark.asyncio
async def test_multiple_connections_and_cross_user_attempt_keys(client, harness):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    second = await join(client)
    third = await join(client, client_id, token="viewer-b")
    assert all(response.status_code == 200 for response in (first, second, third))
    assert len({response.json()["viewer_session_id"] for response in (first, second, third)}) == 3
    assert len(await stored_viewers(harness)) == 3


@pytest.mark.asyncio
async def test_same_attempt_key_is_scoped_to_stream(client, harness):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    second = await join(client, client_id, stream_id=OTHER_STREAM_ID)
    assert first.status_code == second.status_code == 200
    assert first.json()["viewer_session_id"] != second.json()["viewer_session_id"]
    assert len(await stored_viewers(harness)) == 2


@pytest.mark.asyncio
async def test_simultaneous_duplicate_joins_have_one_persisted_row(client, harness):
    client_id = uuid.uuid4()
    responses = await asyncio.gather(*(join(client, client_id) for _ in range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["viewer_session_id"] for response in responses}) == 1
    assert len(await stored_viewers(harness)) == 1


@pytest.mark.asyncio
async def test_heartbeat_renews_only_its_connection(client, harness):
    first, second = await join(client), await join(client)
    data = first.json()
    harness.now[0] += timedelta(seconds=20)
    response = await viewer_action(client, data["viewer_session_id"], "heartbeat", body={})
    assert response.status_code == 200
    renewed = response.json()
    assert renewed["joined_at"] == data["joined_at"]
    assert renewed["viewer_session_id"] == data["viewer_session_id"]
    assert timestamp(renewed["lease_expires_at"]) == NOW + timedelta(seconds=80)
    assert renewed["is_active"] is True
    other = next(row for row in await stored_viewers(harness) if str(row.id) == second.json()["viewer_session_id"])
    assert other.lease_expires_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_lease_renewal_never_moves_backwards(client, harness, monkeypatch):
    first = await join(client)
    data = first.json()
    harness.now[0] += timedelta(seconds=10)
    monkeypatch.setattr(audience_service.settings, "streaming_viewer_lease_seconds", 5)
    response = await viewer_action(client, data["viewer_session_id"], "heartbeat")
    assert response.status_code == 200
    assert response.json()["lease_expires_at"] == data["lease_expires_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [60, 61, 3600])
async def test_expiration_excludes_presence_and_heartbeat_cannot_revive(client, harness, elapsed):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    harness.now[0] += timedelta(seconds=elapsed)
    session_id = first.json()["viewer_session_id"]
    response = await viewer_action(client, session_id, "heartbeat")
    assert response.status_code == 409
    retry = await join(client, client_id)
    assert retry.status_code == 200
    assert retry.json()["is_active"] is False
    assert retry.json()["left_at"] is None
    assert retry.json()["lease_expires_at"] == first.json()["lease_expires_at"]
    reconnect = await join(client)
    assert reconnect.status_code == 200
    assert reconnect.json()["viewer_session_id"] != session_id
    assert reconnect.json()["is_active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["heartbeat", "leave"])
@pytest.mark.parametrize("token", ["viewer-b", "creator"])
async def test_other_viewer_and_creator_cannot_modify_viewer_session(client, harness, action, token):
    first = await join(client)
    harness.now[0] += timedelta(seconds=20)
    response = await viewer_action(client, first.json()["viewer_session_id"], action, token=token)
    assert response.status_code == 404
    viewer, = await stored_viewers(harness)
    assert viewer.left_at is None
    assert viewer.lease_expires_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=60)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["heartbeat", "leave"])
async def test_viewer_session_must_belong_to_supplied_stream(client, harness, action):
    first = await join(client)
    response = await viewer_action(
        client, first.json()["viewer_session_id"], action, stream_id=OTHER_STREAM_ID
    )
    assert response.status_code == 404
    assert (await stored_viewers(harness))[0].left_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["heartbeat", "leave"])
async def test_unknown_viewer_session_is_not_found(client, action):
    assert (await viewer_action(client, uuid.uuid4(), action)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [StreamSessionStatus.ENDED, StreamSessionStatus.FAILED])
async def test_heartbeat_and_join_retry_rejected_after_termination(client, harness, status):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    harness.now[0] += timedelta(seconds=10)
    await change_stream(harness, status=status, ended_at=harness.now[0])
    assert (await viewer_action(client, first.json()["viewer_session_id"], "heartbeat")).status_code == 409
    assert (await join(client, client_id)).status_code == 409
    viewer, = await stored_viewers(harness)
    assert viewer.lease_expires_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_leave_finalizes_once_and_does_not_allow_heartbeat_or_retry_reopen(client, harness):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    session_id = first.json()["viewer_session_id"]
    harness.now[0] += timedelta(seconds=20)
    left = await viewer_action(client, session_id, "leave")
    assert left.status_code == 200
    assert timestamp(left.json()["left_at"]) == harness.now[0]
    assert left.json()["is_active"] is False
    harness.now[0] += timedelta(seconds=5)
    retry = await viewer_action(client, session_id, "leave")
    assert retry.json() == left.json()
    assert (await join(client, client_id)).json() == left.json()
    assert (await viewer_action(client, session_id, "heartbeat")).status_code == 409
    assert len(await stored_viewers(harness)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "elapsed,stream_end,expected",
    [(20, None, 20), (90, None, 60), (90, 40, 40), (90, 80, 60)],
)
async def test_leave_uses_earliest_server_lease_or_broadcast_boundary(
    client, harness, elapsed, stream_end, expected
):
    first = await join(client)
    harness.now[0] += timedelta(seconds=elapsed)
    if stream_end is not None:
        await change_stream(
            harness, status=StreamSessionStatus.ENDED,
            ended_at=NOW + timedelta(seconds=stream_end),
        )
    response = await viewer_action(client, first.json()["viewer_session_id"], "leave")
    assert response.status_code == 200
    assert timestamp(response.json()["left_at"]) == NOW + timedelta(seconds=expected)
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["join", "heartbeat", "leave"])
@pytest.mark.parametrize("field", ["user_id", "joined_at", "left_at", "lease_expires_at"])
async def test_client_cannot_supply_identity_or_server_timestamps(client, harness, action, field):
    body = {field: str(VIEWER_B) if field == "user_id" else NOW.isoformat()}
    if action == "join":
        body["client_session_id"] = str(uuid.uuid4())
        response = await join(client, body=body)
        assert await stored_viewers(harness) == []
    else:
        first = await join(client)
        response = await viewer_action(client, first.json()["viewer_session_id"], action, body=body)
        viewer, = await stored_viewers(harness)
        assert viewer.left_at is None
        assert viewer.user_id == VIEWER_A
        assert viewer.lease_expires_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=60)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_configured_lease_is_used(client, harness, monkeypatch):
    monkeypatch.setattr(audience_service.settings, "streaming_viewer_lease_seconds", 120)
    response = await join(client)
    assert timestamp(response.json()["lease_expires_at"]) == NOW + timedelta(seconds=120)
    harness.now[0] += timedelta(seconds=20)
    renewed = await viewer_action(client, response.json()["viewer_session_id"], "heartbeat")
    assert timestamp(renewed.json()["lease_expires_at"]) == NOW + timedelta(seconds=140)


@pytest.mark.parametrize("seconds", [0, -1, 3601])
def test_lease_configuration_rejects_unsafe_values(seconds):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, streaming_viewer_lease_seconds=seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,limit_action",
    [
        ("join", STREAM_VIEWER_JOIN_ACTION),
        ("heartbeat", STREAM_VIEWER_HEARTBEAT_ACTION),
        ("leave", STREAM_VIEWER_LEAVE_ACTION),
    ],
)
async def test_exact_action_limits_and_retry_after(client, harness, action, limit_action):
    first = await join(client)
    data = first.json()
    limit = STREAM_VIEWER_ACTION_LIMITS[limit_action]
    assert limit.window_seconds == 60
    count = limit.requests - 1 if action == "join" else limit.requests
    for _ in range(count):
        response = (
            await join(client, uuid.UUID(data["client_session_id"]))
            if action == "join"
            else await viewer_action(client, data["viewer_session_id"], action)
        )
        assert response.status_code == 200

    async def attempt(token="viewer-a"):
        return (
            await join(client, uuid.UUID(data["client_session_id"]), token=token)
            if action == "join"
            else await viewer_action(client, data["viewer_session_id"], action, token=token)
        )

    assert_limited(await attempt("viewer-a-new-token"))
    harness.rate_clock[0] += 59.5
    assert_limited(await attempt(), retry_after=1)
    harness.rate_clock[0] += 0.5
    assert (await attempt()).status_code == 200
    assert len(await stored_viewers(harness)) == 1


@pytest.mark.asyncio
async def test_action_limits_follow_user_across_ips_and_connections(client, harness):
    for _ in range(STREAM_VIEWER_ACTION_LIMITS[STREAM_VIEWER_JOIN_ACTION].requests):
        assert (await join(client)).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.41", 1234)),
        base_url="http://test",
    ) as other_ip:
        assert_limited(await join(other_ip, token="viewer-a-new-token"))
        assert (await join(other_ip, token="viewer-b")).status_code == 200


@pytest.mark.asyncio
async def test_failure_attempts_consume_action_quota(client, harness):
    for _ in range(STREAM_VIEWER_ACTION_LIMITS[STREAM_VIEWER_JOIN_ACTION].requests):
        assert (await join(client, stream_id=uuid.uuid4())).status_code == 404
    assert_limited(await join(client))
    assert await stored_viewers(harness) == []


@pytest.mark.asyncio
async def test_global_middleware_is_not_bypassed(client, harness):
    # These invalid bodies consume global request quota but never enter the service.
    for _ in range(60):
        assert (await join(client, body={})).status_code == 422
    assert_limited(await join(client))
    assert await stored_viewers(harness) == []


@pytest.mark.asyncio
async def test_action_buckets_are_independent(client, harness):
    client_id = uuid.uuid4()
    for _ in range(20):
        first = await join(client, client_id)
        assert first.status_code == 200
    assert_limited(await join(client, client_id))
    session_id = first.json()["viewer_session_id"]
    assert (await viewer_action(client, session_id, "heartbeat")).status_code == 200
    assert (await viewer_action(client, session_id, "leave")).status_code == 200


@pytest.mark.asyncio
async def test_leave_only_closes_the_addressed_connection(client, harness):
    first, second = await join(client), await join(client)
    assert (await viewer_action(client, first.json()["viewer_session_id"], "leave")).status_code == 200
    assert (await viewer_action(client, second.json()["viewer_session_id"], "heartbeat")).json()["is_active"] is True


@pytest.mark.asyncio
async def test_concurrent_heartbeat_and_leave_never_reopen(client, harness):
    first = await join(client)
    session_id = first.json()["viewer_session_id"]
    harness.now[0] += timedelta(seconds=20)
    heartbeat, leave = await asyncio.gather(
        viewer_action(client, session_id, "heartbeat"),
        viewer_action(client, session_id, "leave"),
    )
    assert heartbeat.status_code in (200, 409)
    assert leave.status_code == 200
    viewer, = await stored_viewers(harness)
    assert viewer.left_at is not None
    assert (await viewer_action(client, session_id, "heartbeat")).status_code == 409


@pytest.mark.asyncio
async def test_process_exit_prevents_renewal_and_bounds_leave(client, harness, monkeypatch):
    first = await join(client)
    harness.now[0] += timedelta(seconds=20)
    monkeypatch.setattr(streaming_service, "async_session_factory", harness.sessions)
    monkeypatch.setattr(streaming_service, "_utcnow", lambda: harness.now[0])
    await streaming_service._persist_process_exit(STREAM_ID, 1, False)
    harness.now[0] += timedelta(seconds=10)
    session_id = first.json()["viewer_session_id"]
    assert (await viewer_action(client, session_id, "heartbeat")).status_code == 409
    left = await viewer_action(client, session_id, "leave")
    assert timestamp(left.json()["left_at"]) == NOW + timedelta(seconds=20)


@pytest.mark.asyncio
async def test_stop_preserves_process_exit_timestamp_without_locking_across_callback(harness, monkeypatch):
    monkeypatch.setattr(streaming_service, "async_session_factory", harness.sessions)
    monkeypatch.setattr(streaming_service, "_utcnow", lambda: harness.now[0])

    async def stop_with_exit(stream_id, owner_id):
        await streaming_service._persist_process_exit(stream_id, 0, True)
        harness.now[0] += timedelta(seconds=5)
        return True

    manager = SimpleNamespace(stop=AsyncMock(side_effect=stop_with_exit))
    async with harness.sessions() as db:
        result = await streaming_service.StreamingService(db, manager).stop(
            STREAM_ID, harness.users["creator"]
        )
        await db.commit()
    assert result.status == StreamSessionStatus.ENDED
    async with harness.sessions() as db:
        ended_at = await db.scalar(select(StreamSession.ended_at).where(StreamSession.id == STREAM_ID))
        assert ended_at.replace(tzinfo=timezone.utc) == NOW


@pytest.mark.asyncio
async def test_time_is_captured_after_parent_lock(harness, monkeypatch):
    lock_stream = StreamViewerSessionRepository.lock_stream

    async def delayed_lock(repository, stream_id):
        stream = await lock_stream(repository, stream_id)
        harness.now[0] += timedelta(seconds=20)
        return stream

    monkeypatch.setattr(StreamViewerSessionRepository, "lock_stream", delayed_lock)
    async with harness.sessions() as db:
        result = await AudienceService(db).join(STREAM_ID, uuid.uuid4(), harness.users["viewer-a"])
        await db.commit()
    assert result.joined_at == NOW + timedelta(seconds=20)


@pytest.mark.asyncio
async def test_expiry_is_checked_after_viewer_lock(client, harness, monkeypatch):
    first = await join(client)
    lock_owned = StreamViewerSessionRepository.lock_owned

    async def delayed_lock(repository, stream_id, viewer_id, user_id):
        viewer = await lock_owned(repository, stream_id, viewer_id, user_id)
        harness.now[0] = NOW + timedelta(seconds=60)
        return viewer

    monkeypatch.setattr(StreamViewerSessionRepository, "lock_owned", delayed_lock)
    response = await viewer_action(client, first.json()["viewer_session_id"], "heartbeat")
    assert response.status_code == 409
    viewer, = await stored_viewers(harness)
    assert viewer.lease_expires_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_future_join_is_not_active_and_cannot_be_renewed(client, harness):
    client_id = uuid.uuid4()
    first = await join(client, client_id)
    harness.now[0] = NOW - timedelta(seconds=1)
    retry = await join(client, client_id)
    assert retry.status_code == 200
    assert retry.json()["is_active"] is False
    assert (await viewer_action(client, first.json()["viewer_session_id"], "heartbeat")).status_code == 409
    assert (await viewer_action(client, first.json()["viewer_session_id"], "leave")).status_code == 409
    assert (await stored_viewers(harness))[0].left_at is None


@pytest.mark.asyncio
async def test_simultaneous_leaves_keep_one_finalized_interval(client, harness):
    first = await join(client)
    harness.now[0] += timedelta(seconds=20)
    responses = await asyncio.gather(
        *(viewer_action(client, first.json()["viewer_session_id"], "leave") for _ in range(4))
    )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["left_at"] for response in responses}) == 1
    assert timestamp(responses[0].json()["left_at"]) == NOW + timedelta(seconds=20)
    assert len(await stored_viewers(harness)) == 1


@pytest.mark.asyncio
async def test_repository_locks_parent_and_scopes_viewer_for_postgres():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.scalar_one_or_none.return_value = None
    repository = StreamViewerSessionRepository(db)
    await repository.lock_stream(STREAM_ID)
    statement = db.execute.call_args.args[0]
    assert str(statement.compile(dialect=postgresql.dialect())).endswith("FOR UPDATE")
    assert statement.get_execution_options()["populate_existing"] is True
    viewer_id = uuid.uuid4()
    await repository.lock_owned(STREAM_ID, viewer_id, VIEWER_A)
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE" in sql
    for identity in (STREAM_ID, viewer_id, VIEWER_A):
        assert str(identity) in sql


@pytest.mark.asyncio
async def test_postgres_insert_conflict_does_not_update_existing_attempt():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute = AsyncMock(return_value=MagicMock())
    viewer = StreamViewerSession(id=uuid.uuid4())
    db.execute.return_value.scalar_one_or_none.return_value = viewer
    result = await StreamViewerSessionRepository(db).create_attempt(
        stream_id=STREAM_ID, user_id=VIEWER_A, client_session_id=uuid.uuid4(),
        joined_at=NOW, lease_expires_at=NOW + timedelta(seconds=60),
    )
    assert result is viewer
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (stream_session_id, user_id, client_session_id) DO NOTHING" in sql
    assert "DO UPDATE" not in sql


@pytest.mark.asyncio
async def test_persistence_failure_is_not_reported_as_success(client, harness, monkeypatch):
    monkeypatch.setattr(
        StreamViewerSessionRepository, "create_attempt",
        AsyncMock(side_effect=RuntimeError("Simulated persistence failure")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, raise_app_exceptions=False),
        base_url="http://test",
    ) as failing_client:
        response = await join(failing_client)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert await stored_viewers(harness) == []


def test_openapi_exposes_only_presence_contracts(harness):
    schema = harness.app.openapi()
    paths = schema["paths"]
    assert "post" in paths["/api/streams/{stream_id}/viewers/join"]
    for action in ("heartbeat", "leave"):
        assert "post" in paths[f"/api/streams/{{stream_id}}/viewers/{{viewer_session_id}}/{action}"]
    join_schema = schema["components"]["schemas"]["ViewerJoinRequest"]
    assert set(join_schema["properties"]) == {"client_session_id"}
    assert join_schema["required"] == ["client_session_id"]
    assert join_schema["additionalProperties"] is False
    update_schema = schema["components"]["schemas"]["ViewerSessionUpdateRequest"]
    assert update_schema["properties"] == {}
    assert update_schema["additionalProperties"] is False


async def persist_intervals(harness, intervals, *, stream_id=STREAM_ID, left_at=None):
    """Persist (user, join seconds, lease seconds) relative to NOW."""
    async with harness.sessions() as db:
        repository = StreamViewerSessionRepository(db)
        for user_id, joined, lease in intervals:
            viewer = await repository.create_attempt(
                stream_id=stream_id,
                user_id=user_id,
                client_session_id=uuid.uuid4(),
                joined_at=NOW + timedelta(seconds=joined),
                lease_expires_at=NOW + timedelta(seconds=lease),
            )
            if left_at is not None:
                await repository.finalize(viewer, NOW + timedelta(seconds=left_at))
        await db.commit()


async def audience_totals(harness, start=NOW, end=NOW + timedelta(seconds=60)):
    async with harness.sessions() as db:
        repository = StreamViewerSessionRepository(db)
        return (
            await repository.count_unique_viewers(STREAM_ID, start, end),
            await repository.total_watch_duration(STREAM_ID, start, end),
            await repository.peak_concurrent_viewers(STREAM_ID, start, end),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intervals, expected",
    [
        ([], (0, 0.0, 0)),
        ([(VIEWER_A, 0, 10)], (1, 10.0, 1)),
        ([(VIEWER_A, 0, 10), (VIEWER_B, 0, 10)], (2, 20.0, 2)),
        ([(VIEWER_A, 0, 10), (VIEWER_A, 0, 10)], (1, 10.0, 1)),
        ([(VIEWER_A, 0, 10), (VIEWER_A, 5, 15)], (1, 15.0, 1)),
        ([(VIEWER_A, 0, 20), (VIEWER_A, 5, 10)], (1, 20.0, 1)),
        ([(VIEWER_A, 0, 5), (VIEWER_A, 10, 15)], (1, 10.0, 1)),
        (
            [(VIEWER_A, 0, 5), (VIEWER_A, 10, 15), (VIEWER_B, 5, 10)],
            (2, 15.0, 1),
        ),
        ([(VIEWER_A, 0, 5), (VIEWER_A, 5, 10)], (1, 10.0, 1)),
        ([(VIEWER_A, 0, 5), (VIEWER_B, 5, 10)], (2, 10.0, 1)),
        (
            [(VIEWER_A, 0, 10), (VIEWER_A, 5, 15), (VIEWER_B, 7, 12),
             (CREATOR, 10, 20)],
            (3, 30.0, 3),
        ),
        # Reversed insertion order must not change merging or the sweep.
        (
            [(VIEWER_A, 10, 20), (VIEWER_A, 5, 15), (VIEWER_A, 0, 10),
             (VIEWER_B, 3, 7), (VIEWER_B, 12, 17)],
            (2, 29.0, 2),
        ),
        ([(VIEWER_A, 0.25, 1.75)], (1, 1.5, 1)),
    ],
    ids=[
        "zero", "one", "unique-users", "identical-tabs", "overlap", "nested-tabs",
        "gaps", "gap-does-not-inflate-peak", "adjacent-same-user",
        "simultaneous-leave-join", "three-users-overlap",
        "unsorted-overlap-and-gaps", "fractional-seconds",
    ],
)
async def test_repository_audience_interval_union(harness, intervals, expected):
    await persist_intervals(harness, intervals)
    assert await audience_totals(harness) == expected


@pytest.mark.asyncio
async def test_repository_reporting_clips_before_union_and_excludes_boundary_touches(harness):
    await persist_intervals(
        harness,
        [(VIEWER_A, -20, 15), (VIEWER_A, 10, 40), (VIEWER_B, 5, 25),
         (CREATOR, 0, 10), (CREATOR, 20, 30)],
    )
    # The creator only touches the period; A's two tabs union to the full period.
    assert await audience_totals(
        harness, NOW + timedelta(seconds=10), NOW + timedelta(seconds=20)
    ) == (2, 20.0, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [StreamSessionStatus.ACTIVE, StreamSessionStatus.ENDED])
async def test_repository_stream_start_end_clipping(harness, status):
    await persist_intervals(
        harness,
        [(VIEWER_A, -20, 40), (VIEWER_B, 10, 30), (CREATOR, -10, 5),
         (CREATOR, 20, 30)],
    )
    await change_stream(
        harness, status=status,
        started_at=NOW + timedelta(seconds=5), ended_at=NOW + timedelta(seconds=20),
    )
    assert await audience_totals(harness) == (2, 25.0, 2)
    async with harness.sessions() as db:
        repository = StreamViewerSessionRepository(db)
        assert await repository.count_current_viewers(
            STREAM_ID, NOW + timedelta(seconds=10)
        ) == (2 if status == StreamSessionStatus.ACTIVE else 0)
        for second in (0, 20, 30):
            assert await repository.count_current_viewers(
                STREAM_ID, NOW + timedelta(seconds=second)
            ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("left_at, expected", [(None, 10.0), (4, 4.0), (0, 0.0), (10, 10.0)])
async def test_repository_expired_lease_and_explicit_leave(harness, left_at, expected):
    await persist_intervals(harness, [(VIEWER_A, 0, 10)], left_at=left_at)
    assert await audience_totals(harness) == (
        int(expected > 0), expected, int(expected > 0)
    )
    async with harness.sessions() as db:
        repository = StreamViewerSessionRepository(db)
        boundary = left_at if left_at is not None else 10
        assert await repository.count_current_viewers(
            STREAM_ID, NOW + timedelta(seconds=boundary)
        ) == 0
        if boundary > 0:
            assert await repository.count_current_viewers(
                STREAM_ID, NOW + timedelta(seconds=boundary, microseconds=-1)
            ) == 1
        assert await repository.count_unique_viewers(
            STREAM_ID, NOW + timedelta(seconds=10), NOW + timedelta(seconds=60)
        ) == 0


@pytest.mark.asyncio
async def test_repository_current_concurrency_distinct_users_and_half_open_boundaries(harness):
    await persist_intervals(
        harness, [(VIEWER_A, 0, 10), (VIEWER_A, 2, 12), (VIEWER_B, 5, 15)]
    )
    async with harness.sessions() as db:
        repository = StreamViewerSessionRepository(db)
        for second, expected in [(-1, 0), (0, 1), (2, 1), (5, 2), (10, 2), (12, 1), (15, 0)]:
            assert await repository.count_current_viewers(
                STREAM_ID, NOW + timedelta(seconds=second)
            ) == expected
    assert await audience_totals(harness) == (2, 22.0, 2)


@pytest.mark.asyncio
async def test_repository_current_simultaneous_leaves_and_joins(harness):
    await persist_intervals(
        harness,
        [(VIEWER_A, 0, 5), (VIEWER_A, 5, 10), (VIEWER_B, 0, 5), (CREATOR, 5, 10)],
    )
    async with harness.sessions() as db:
        assert await StreamViewerSessionRepository(db).count_current_viewers(
            STREAM_ID, NOW + timedelta(seconds=5)
        ) == 2
    assert await audience_totals(harness) == (3, 20.0, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"status": StreamSessionStatus.PENDING},
        {"status": StreamSessionStatus.FAILED},
        {"started_at": None},
        {"started_at": NOW + timedelta(seconds=20), "ended_at": NOW + timedelta(seconds=10)},
        {"ended_at": NOW},
        {"started_at": NOW + timedelta(seconds=60)},
    ],
    ids=["pending", "failed", "no-start", "inverted-stream", "ended-before-period", "future-start"],
)
async def test_repository_excludes_nonqualifying_parent_streams(harness, values):
    await persist_intervals(harness, [(VIEWER_A, 0, 30)])
    await change_stream(harness, **values)
    assert await audience_totals(harness) == (0, 0.0, 0)
    async with harness.sessions() as db:
        assert await StreamViewerSessionRepository(db).count_current_viewers(
            STREAM_ID, NOW + timedelta(seconds=5)
        ) == 0


@pytest.mark.asyncio
async def test_repository_scopes_all_metrics_to_parent_stream(harness):
    await persist_intervals(harness, [(VIEWER_A, 0, 10)])
    await persist_intervals(
        harness, [(VIEWER_B, 0, 60), (CREATOR, 0, 60)], stream_id=OTHER_STREAM_ID
    )
    assert await audience_totals(harness) == (1, 10.0, 1)
    async with harness.sessions() as db:
        repository = StreamViewerSessionRepository(db)
        assert await repository.count_current_viewers(STREAM_ID, NOW) == 1
        missing_id = uuid.uuid4()
        end = NOW + timedelta(seconds=60)
        assert await repository.count_unique_viewers(missing_id, NOW, end) == 0
        assert await repository.total_watch_duration(missing_id, NOW, end) == 0.0
        assert await repository.peak_concurrent_viewers(missing_id, NOW, end) == 0
        assert await repository.count_current_viewers(missing_id, NOW) == 0


@pytest.mark.asyncio
async def test_repository_zero_current_viewers(harness):
    async with harness.sessions() as db:
        assert await StreamViewerSessionRepository(db).count_current_viewers(STREAM_ID, NOW) == 0


@pytest.mark.asyncio
async def test_repository_normalizes_reporting_and_point_timezones(harness):
    await persist_intervals(harness, [(VIEWER_A, 0, 10)])
    offset = timezone(timedelta(hours=2))
    for start in (NOW.replace(tzinfo=None), NOW.astimezone(offset)):
        end = start + timedelta(seconds=10)
        assert await audience_totals(harness, start, end) == (1, 10.0, 1)
        async with harness.sessions() as db:
            assert await StreamViewerSessionRepository(db).count_current_viewers(
                STREAM_ID, start
            ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["count_unique_viewers", "total_watch_duration", "peak_concurrent_viewers"]
)
@pytest.mark.parametrize("seconds", [0, -1])
async def test_repository_rejects_invalid_reporting_period(method, seconds):
    db = MagicMock()
    db.execute = AsyncMock()
    repository = StreamViewerSessionRepository(db)
    with pytest.raises(ValueError, match="Reporting end must be after reporting start"):
        await getattr(repository, method)(STREAM_ID, NOW, NOW + timedelta(seconds=seconds))
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["count_unique_viewers", "total_watch_duration",
               "count_current_viewers", "peak_concurrent_viewers"]
)
async def test_repository_aggregation_does_not_hide_database_errors(method):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("Audience query failed"))
    args = (STREAM_ID, NOW) if method == "count_current_viewers" else (
        STREAM_ID, NOW, NOW + timedelta(seconds=60)
    )
    with pytest.raises(RuntimeError, match="Audience query failed"):
        await getattr(StreamViewerSessionRepository(db), method)(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["count_unique_viewers", "total_watch_duration",
               "count_current_viewers", "peak_concurrent_viewers"]
)
async def test_repository_aggregation_queries_compile_for_postgres(method):
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.all.return_value = []
    db.execute.return_value.scalar_one.return_value = 0
    args = (STREAM_ID, NOW) if method == "count_current_viewers" else (
        STREAM_ID, NOW, NOW + timedelta(seconds=60)
    )
    await getattr(StreamViewerSessionRepository(db), method)(*args)
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert str(STREAM_ID) in sql
    assert "JOIN stream_sessions ON stream_sessions.id = stream_viewer_sessions.stream_session_id" in sql
    assert "stream_viewer_sessions.lease_expires_at >" in sql
    assert "stream_viewer_sessions.left_at IS NULL" in sql
    assert "stream_sessions.started_at" in sql
    assert "stream_sessions.ended_at IS NULL" in sql
    assert "FOR UPDATE" not in sql
    if method == "count_current_viewers":
        assert "count(distinct(stream_viewer_sessions.user_id))" in sql
        assert "stream_sessions.status = 'active'" in sql
        assert "stream_viewer_sessions.joined_at <=" in sql
    else:
        assert "stream_sessions.status IN ('active', 'ended')" in sql
        assert "stream_viewer_sessions.joined_at <" in sql
