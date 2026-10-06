"""Persistence, authorization, and creator analytics tests for stream chat."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import Column, MetaData, String, Table, Uuid, event, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.dependencies import get_db_session
from app.main import create_app
from app.models.streaming import (
    StreamChatMessage,
    StreamDestination,
    StreamSession,
    StreamSessionStatus,
    StreamViewerSession,
)
from app.models.user import User
from app.rate_limits import (
    STREAM_CHAT_SEND_ACTION,
    STREAM_VIEWER_ACTION_LIMITS,
    ActionLimit,
    ActionRateLimiter,
)
from features.auth.middleware import get_current_active_user
from features.streaming import chat_service
from repositories.stream_chat_repository import StreamChatRepository

CREATOR = uuid.UUID(int=1)
OTHER_CREATOR = uuid.UUID(int=2)
VIEWER = uuid.UUID(int=3)
OTHER_VIEWER = uuid.UUID(int=4)
NOW = datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def chat_harness(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stream-chat.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata = MetaData()
    Table("users", metadata, Column("id", Uuid(native_uuid=False), primary_key=True))
    Table(
        "connected_stream_accounts",
        metadata,
        Column("id", Uuid(native_uuid=False), primary_key=True),
    )
    Table(
        "messages",
        metadata,
        Column("id", Uuid(native_uuid=False), primary_key=True),
        Column("conversation_id", Uuid(native_uuid=False), nullable=False),
        Column("sender_id", Uuid(native_uuid=False), nullable=False),
        Column("body", String, nullable=False),
    )
    for model in (
        StreamSession,
        StreamDestination,
        StreamViewerSession,
        StreamChatMessage,
    ):
        model.__table__.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PG_UUID):
                column.type = Uuid(native_uuid=False)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            metadata.tables["users"].insert(),
            [{"id": user_id} for user_id in (CREATOR, OTHER_CREATOR, VIEWER, OTHER_VIEWER)],
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def current_user(request: Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        users = {
            "creator": CREATOR,
            "viewer": VIEWER,
            "other-viewer": OTHER_VIEWER,
            "other-creator": OTHER_CREATOR,
        }
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
    limiter_clock = [100.0]
    monkeypatch.setattr(
        chat_service,
        "stream_chat_action_limiter",
        ActionRateLimiter(STREAM_VIEWER_ACTION_LIMITS, clock=lambda: limiter_clock[0]),
    )
    try:
        yield SimpleNamespace(
            app=app,
            engine=engine,
            sessions=sessions,
            metadata=metadata,
            limiter_clock=limiter_clock,
        )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(chat_harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat_harness.app),
        base_url="http://test",
    ) as test_client:
        yield test_client


async def add_stream(
    db,
    owner_id=CREATOR,
    *,
    status=StreamSessionStatus.ACTIVE,
    started_at=NOW - timedelta(minutes=1),
    ended_at=None,
    created_at=NOW - timedelta(minutes=1),
):
    stream = StreamSession(
        id=uuid.uuid4(),
        owner_id=owner_id,
        input_source="input.mp4",
        status=status,
        created_at=created_at,
        started_at=started_at,
        ended_at=ended_at,
    )
    db.add(stream)
    await db.flush()
    return stream


async def add_viewer(db, stream_id, user_id=VIEWER, *, joined_at=NOW - timedelta(seconds=30)):
    viewer = StreamViewerSession(
        id=uuid.uuid4(),
        stream_session_id=stream_id,
        user_id=user_id,
        client_session_id=uuid.uuid4(),
        joined_at=joined_at,
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    db.add(viewer)
    await db.flush()
    return viewer


async def post_chat(client, stream_id, token, body=" hello "):
    return await client.post(
        f"/api/streams/{stream_id}/chat",
        json={"body": body},
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


@pytest.mark.asyncio
async def test_active_owner_and_joined_viewer_can_persist_stream_chat(client, chat_harness):
    async with chat_harness.sessions() as db:
        stream = await add_stream(db)
        await add_viewer(db, stream.id)
        await db.commit()

    owner_response = await post_chat(client, stream.id, "creator")
    viewer_response = await post_chat(client, stream.id, "viewer", " viewer message ")

    assert owner_response.status_code == 201
    assert viewer_response.status_code == 201
    assert owner_response.json()["body"] == "hello"
    assert viewer_response.json()["body"] == "viewer message"
    assert owner_response.json()["stream_id"] == str(stream.id)
    async with chat_harness.sessions() as db:
        rows = (await db.scalars(select(StreamChatMessage))).all()
        assert len(rows) == 2
        assert await StreamChatRepository(db).count_for_stream(stream.id) == 2


@pytest.mark.asyncio
async def test_stream_chat_rejects_missing_inactive_unjoined_and_anonymous_senders(
    client, chat_harness
):
    async with chat_harness.sessions() as db:
        active = await add_stream(db)
        ended = await add_stream(
            db,
            status=StreamSessionStatus.ENDED,
            ended_at=NOW - timedelta(seconds=1),
        )
        await db.commit()

    assert (await post_chat(client, uuid.uuid4(), "creator")).status_code == 404
    assert (await post_chat(client, ended.id, "creator")).status_code == 409
    assert (await post_chat(client, active.id, "other-viewer")).status_code == 403
    assert (await post_chat(client, active.id, None)).status_code == 401


@pytest.mark.asyncio
async def test_stream_chat_endpoint_applies_action_specific_rate_limit(
    client, chat_harness, monkeypatch
):
    async with chat_harness.sessions() as db:
        stream = await add_stream(db)
        await db.commit()
    monkeypatch.setattr(
        chat_service,
        "stream_chat_action_limiter",
        ActionRateLimiter(
            {STREAM_CHAT_SEND_ACTION: ActionLimit(1)},
            clock=lambda: chat_harness.limiter_clock[0],
        ),
    )

    assert (await post_chat(client, stream.id, "creator", "first")).status_code == 201
    limited = await post_chat(client, stream.id, "creator", "second")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) == 60


@pytest.mark.asyncio
async def test_creator_chat_totals_scope_owner_streams_and_half_open_period(
    chat_harness,
):
    start = NOW
    end = NOW + timedelta(seconds=10)
    async with chat_harness.sessions() as db:
        first_stream = await add_stream(db, created_at=start - timedelta(minutes=1))
        second_stream = await add_stream(
            db,
            status=StreamSessionStatus.ENDED,
            ended_at=end + timedelta(seconds=1),
            created_at=start - timedelta(minutes=1),
        )
        other_stream = await add_stream(
            db, owner_id=OTHER_CREATOR, created_at=start - timedelta(minutes=1)
        )
        ending_at_message = await add_stream(
            db,
            status=StreamSessionStatus.ENDED,
            ended_at=start + timedelta(seconds=3),
            created_at=start - timedelta(minutes=1),
        )
        starts_after_message = await add_stream(
            db,
            status=StreamSessionStatus.ENDED,
            started_at=start + timedelta(seconds=4),
            ended_at=end + timedelta(seconds=1),
            created_at=start - timedelta(minutes=1),
        )
        await db.flush()
        messages = [
            (first_stream.id, VIEWER, start),
            (first_stream.id, VIEWER, start + timedelta(seconds=1)),
            (second_stream.id, OTHER_VIEWER, start + timedelta(seconds=2)),
            (ending_at_message.id, VIEWER, start + timedelta(seconds=3)),
            (starts_after_message.id, OTHER_VIEWER, start + timedelta(seconds=3)),
            (other_stream.id, OTHER_VIEWER, start + timedelta(seconds=4)),
            (first_stream.id, OTHER_VIEWER, end),
            (first_stream.id, OTHER_VIEWER, start - timedelta(microseconds=1)),
        ]
        await db.execute(
            chat_harness.metadata.tables["messages"].insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "conversation_id": uuid.uuid4(),
                    "sender_id": VIEWER,
                    "body": "this is an ordinary direct message",
                }
            ],
        )
        for stream_id, user_id, created_at in messages:
            db.add(
                StreamChatMessage(
                    id=uuid.uuid4(),
                    stream_session_id=stream_id,
                    user_id=user_id,
                    body="chat",
                    created_at=created_at,
                )
            )
        await db.commit()

        totals = await StreamChatRepository(db).creator_chat_totals(
            creator_id=CREATOR, start=start, end=end
        )

    assert totals == {"stream_chat_messages": 3, "unique_stream_chatters": 2}


@pytest.mark.asyncio
async def test_zero_chat_activity_and_reporting_period_validation(chat_harness):
    start = NOW
    end = start + timedelta(days=1)
    async with chat_harness.sessions() as db:
        result = await StreamChatRepository(db).creator_chat_totals(
            creator_id=CREATOR, start=start, end=end
        )
        assert result == {"stream_chat_messages": 0, "unique_stream_chatters": 0}
        with pytest.raises(ValueError, match="Reporting end"):
            await StreamChatRepository(db).creator_chat_totals(
                creator_id=CREATOR, start=end, end=start
            )


@pytest.mark.asyncio
async def test_stream_chat_does_not_depend_on_direct_message_records(chat_harness):
    async with chat_harness.sessions() as db:
        stream = await add_stream(db)
        await db.execute(
            chat_harness.metadata.tables["messages"].insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "conversation_id": uuid.uuid4(),
                    "sender_id": VIEWER,
                    "body": "ordinary DM",
                }
            ],
        )
        await db.commit()
    assert "stream_chat_messages" in chat_harness.metadata.tables
    async with chat_harness.sessions() as db:
        result = await StreamChatRepository(db).creator_chat_totals(
            creator_id=CREATOR,
            start=NOW - timedelta(minutes=1),
            end=NOW + timedelta(minutes=1),
        )
    assert result == {"stream_chat_messages": 0, "unique_stream_chatters": 0}


def test_stream_chat_message_table_has_expected_keys_and_indexes():
    table = StreamChatMessage.__table__
    assert {"id", "stream_session_id", "user_id", "body", "created_at"} <= set(table.columns.keys())
    assert {foreign_key.target_fullname for foreign_key in table.foreign_keys} == {
        "stream_sessions.id",
        "users.id",
    }
    assert {index.name for index in table.indexes} >= {
        "ix_stream_chat_messages_stream_created",
        "ix_stream_chat_messages_sender_stream",
    }
