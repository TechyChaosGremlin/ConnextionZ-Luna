"""Persisted native subscription actions, authorization, idempotency, and reporting."""

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
from sqlalchemy import Column, MetaData, Table, Uuid, event, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.dependencies import get_db_session
from app.main import create_app
from app.models.analytics import InteractionSignal, SignalType
from app.models.social import Follow
from app.models.streaming import (
    StreamDestination,
    StreamSession,
    StreamSessionStatus,
    StreamSubscription,
)
from app.models.user import AccountStatus, User
from app.rate_limits import (
    STREAM_SUBSCRIBE_ACTION,
    STREAM_VIEWER_ACTION_LIMITS,
    ActionLimit,
    ActionRateLimiter,
)
from features.auth.middleware import get_current_active_user, get_current_user
from features.streaming import subscription_service
from repositories.stream_subscription_repository import StreamSubscriptionRepository

CREATOR, OTHER_CREATOR, SUBSCRIBER, OTHER_SUBSCRIBER = [uuid.UUID(int=i) for i in range(1, 5)]
START = datetime(2026, 10, 5, tzinfo=timezone.utc)
END = START + timedelta(days=1)


@pytest_asyncio.fixture
async def subscriptions(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'subscriptions.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for name in ("users", "connected_stream_accounts", "posts"):
        Table(name, metadata, Column("id", Uuid(native_uuid=False), primary_key=True))
    for model in (StreamSession, StreamDestination, StreamSubscription, Follow, InteractionSignal):
        model.__table__.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PG_UUID):
                column.type = Uuid(native_uuid=False)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            metadata.tables["users"].insert(),
            [
                {"id": identity}
                for identity in (CREATOR, OTHER_CREATOR, SUBSCRIBER, OTHER_SUBSCRIBER)
            ],
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def current_user(request: Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        users = {"subscriber": SUBSCRIBER, "other": OTHER_SUBSCRIBER, "creator": CREATOR}
        if token not in users:
            raise HTTPException(status_code=401, detail="Authentication required")
        return User(id=users[token])

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
    monkeypatch.setattr(
        subscription_service,
        "stream_subscription_action_limiter",
        ActionRateLimiter(STREAM_VIEWER_ACTION_LIMITS, clock=lambda: 100.0),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield SimpleNamespace(app=app, sessions=sessions, client=client, metadata=metadata)
    finally:
        await engine.dispose()


async def add_stream(db, owner=CREATOR):
    stream = StreamSession(
        owner_id=owner,
        input_source="input.mp4",
        status=StreamSessionStatus.ENDED,
        started_at=START - timedelta(days=1),
        ended_at=START,
    )
    db.add(stream)
    await db.flush()
    return stream


async def subscribe(harness, stream_id, *, creator=CREATOR, token="subscriber", **extra):
    return await harness.client.post(
        f"/api/streams/{stream_id}/subscriptions",
        json={"creator_id": str(creator), **extra},
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


@pytest.mark.asyncio
async def test_valid_subscription_is_persisted_and_retries_keep_original_id_and_time(subscriptions):
    async with subscriptions.sessions() as db:
        stream = await add_stream(db)
        await db.commit()
    first = await subscribe(subscriptions, stream.id)
    retry = await subscribe(subscriptions, stream.id)
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert first.json()["stream_id"] == str(stream.id)
    assert first.json()["user_id"] == str(SUBSCRIBER)
    assert first.json()["creator_id"] == str(CREATOR)
    assert uuid.UUID(first.json()["id"]).version == 7
    async with subscriptions.sessions() as db:
        rows = (await db.scalars(select(StreamSubscription))).all()
        assert len(rows) == 1
        assert rows[0].id == uuid.UUID(first.json()["id"])
        assert rows[0].created_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, expected",
    [("anonymous", 401), ("missing", 404), ("wrong-owner", 403), ("self", 403)],
)
async def test_invalid_subscription_is_rejected_without_persisting(subscriptions, case, expected):
    async with subscriptions.sessions() as db:
        stream = await add_stream(db)
        await db.commit()
    response = await subscribe(
        subscriptions,
        uuid.uuid4() if case == "missing" else stream.id,
        creator=OTHER_CREATOR if case == "wrong-owner" else CREATOR,
        token=None if case == "anonymous" else "creator" if case == "self" else "subscriber",
    )
    assert response.status_code == expected
    async with subscriptions.sessions() as db:
        assert (await db.scalars(select(StreamSubscription))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [AccountStatus.SUSPENDED, AccountStatus.BANNED, AccountStatus.PENDING_VERIFICATION]
)
async def test_subscription_requires_active_account(subscriptions, status):
    async def inactive_user():
        return User(id=SUBSCRIBER, status=status)

    subscriptions.app.dependency_overrides.pop(get_current_active_user)
    subscriptions.app.dependency_overrides[get_current_user] = inactive_user
    response = await subscribe(subscriptions, uuid.uuid4())
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Account is not active"
    async with subscriptions.sessions() as db:
        assert (await db.scalars(select(StreamSubscription))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": str(OTHER_SUBSCRIBER)},
        {"created_at": START.isoformat()},
        {"platform": "twitch"},
    ],
)
async def test_cannot_spoof_subscriber_timestamp_or_external_platform(subscriptions, extra):
    async with subscriptions.sessions() as db:
        stream = await add_stream(db)
        await db.commit()
    assert (await subscribe(subscriptions, stream.id, **extra)).status_code == 422
    async with subscriptions.sessions() as db:
        assert (await db.scalars(select(StreamSubscription))).all() == []


@pytest.mark.asyncio
async def test_subscriptions_use_existing_streaming_action_limiter(subscriptions, monkeypatch):
    async with subscriptions.sessions() as db:
        stream = await add_stream(db)
        await db.commit()
    monkeypatch.setattr(
        subscription_service,
        "stream_subscription_action_limiter",
        ActionRateLimiter({STREAM_SUBSCRIBE_ACTION: ActionLimit(1)}, clock=lambda: 100.0),
    )
    assert (await subscribe(subscriptions, stream.id)).status_code == 200
    response = await subscribe(subscriptions, stream.id)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert (await subscribe(subscriptions, stream.id, token="other")).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_subscription_retries_are_database_idempotent(subscriptions):
    async with subscriptions.sessions() as db:
        stream = await add_stream(db)
        await db.commit()

    async def write():
        async with subscriptions.sessions() as db:
            record = await StreamSubscriptionRepository(db).subscribe(
                stream_id=stream.id, creator_id=CREATOR, user_id=SUBSCRIBER
            )
            await db.commit()
            return record.id

    ids = await asyncio.gather(write(), write())
    assert ids[0] == ids[1]
    async with subscriptions.sessions() as db:
        assert len((await db.scalars(select(StreamSubscription))).all()) == 1


@pytest.mark.asyncio
async def test_reporting_scopes_creator_window_and_distinct_subscribers_across_streams(
    subscriptions,
):
    async with subscriptions.sessions() as db:
        streams = [await add_stream(db) for _ in range(5)]
        other = await add_stream(db, OTHER_CREATOR)
        for stream, user, at in (
            (streams[0], SUBSCRIBER, START),
            (streams[1], SUBSCRIBER, START + timedelta(seconds=1)),
            (streams[1], OTHER_SUBSCRIBER, END - timedelta(microseconds=1)),
            (streams[2], SUBSCRIBER, START - timedelta(microseconds=1)),
            (streams[3], OTHER_SUBSCRIBER, END),
            (streams[4], OTHER_SUBSCRIBER, END + timedelta(microseconds=1)),
            (other, SUBSCRIBER, START),
        ):
            db.add(StreamSubscription(stream_session_id=stream.id, user_id=user, created_at=at))
        db.add(Follow(follower_id=SUBSCRIBER, following_id=CREATOR))
        db.add_all(
            [
                InteractionSignal(
                    user_id=OTHER_SUBSCRIBER,
                    creator_id=CREATOR,
                    signal_type=SignalType.FOLLOW,
                    created_at=START,
                ),
                InteractionSignal(
                    user_id=OTHER_SUBSCRIBER,
                    creator_id=CREATOR,
                    stream_session_id=streams[0].id,
                    signal_type=SignalType.FOLLOW,
                    created_at=START,
                ),
            ]
        )
        await db.commit()
        repository = StreamSubscriptionRepository(db)
        assert await repository.creator_subscription_totals(
            creator_id=CREATOR, start=START, end=END
        ) == {"stream_subscriptions": 3, "unique_stream_subscribers": 2}
        assert await repository.creator_subscription_totals(
            creator_id=OTHER_CREATOR, start=START, end=END
        ) == {"stream_subscriptions": 1, "unique_stream_subscribers": 1}


@pytest.mark.asyncio
async def test_zero_subscriptions_and_invalid_reporting_windows(subscriptions):
    async with subscriptions.sessions() as db:
        await add_stream(db)
        db.add(Follow(follower_id=SUBSCRIBER, following_id=CREATOR))
        await db.commit()
        repository = StreamSubscriptionRepository(db)
        assert await repository.creator_subscription_totals(
            creator_id=CREATOR, start=START, end=END
        ) == {"stream_subscriptions": 0, "unique_stream_subscribers": 0}
        for start, end in ((END, START), (START, START)):
            with pytest.raises(ValueError, match="Reporting end"):
                await repository.creator_subscription_totals(
                    creator_id=CREATOR, start=start, end=end
                )


@pytest.mark.asyncio
async def test_subscription_table_enforces_user_stream_foreign_keys_and_uniqueness(subscriptions):
    async with subscriptions.sessions() as db:
        stream = await add_stream(db)
        await db.commit()
        for stream_id, user_id in ((uuid.uuid4(), SUBSCRIBER), (stream.id, uuid.uuid4())):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    db.add(StreamSubscription(stream_session_id=stream_id, user_id=user_id))
                    await db.flush()
        db.add(StreamSubscription(stream_session_id=stream.id, user_id=SUBSCRIBER))
        await db.commit()
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(StreamSubscription(stream_session_id=stream.id, user_id=SUBSCRIBER))
                await db.flush()


def test_subscription_model_uses_uuid_foreign_keys_indexes_and_relationship():
    table = StreamSubscription.__table__
    assert {"id", "stream_session_id", "user_id", "created_at"} <= set(table.columns.keys())
    assert {fk.target_fullname for fk in table.foreign_keys} == {"users.id", "stream_sessions.id"}
    assert all(fk.ondelete == "CASCADE" for fk in table.foreign_keys)
    assert all(
        isinstance(table.c[name].type, PG_UUID) for name in ("id", "user_id", "stream_session_id")
    )
    assert {index.name for index in table.indexes} == {
        "ix_stream_subscriptions_stream_created",
        "ix_stream_subscriptions_user",
    }
    assert StreamSubscription.stream_session.property.back_populates == "subscriptions"


@pytest.mark.asyncio
async def test_postgresql_write_locks_parent_and_uses_conflict_safe_insert():
    stream_id = uuid.uuid4()
    subscription = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(owner_id=CREATOR)),
            SimpleNamespace(scalar_one_or_none=lambda: subscription),
        ]
    )
    result = await StreamSubscriptionRepository(db).subscribe(
        stream_id=stream_id, creator_id=CREATOR, user_id=SUBSCRIBER
    )
    assert result is subscription
    parent, insertion = [call.args[0] for call in db.execute.await_args_list]
    assert "FOR UPDATE" in str(parent.compile(dialect=postgresql.dialect()))
    compiled = insertion.compile(dialect=postgresql.dialect())
    assert "ON CONFLICT (stream_session_id, user_id) DO NOTHING" in str(compiled)
    assert "RETURNING stream_subscriptions" in str(compiled)
    assert compiled.params["stream_session_id"] == stream_id
    assert compiled.params["user_id"] == SUBSCRIBER


@pytest.mark.asyncio
async def test_reporting_database_errors_propagate_instead_of_returning_zero():
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await StreamSubscriptionRepository(db).creator_subscription_totals(
            creator_id=CREATOR, start=START, end=END
        )
