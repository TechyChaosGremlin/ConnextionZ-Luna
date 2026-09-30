"""Queries for persisted streaming sessions."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.streaming import StreamPlatform, StreamSessionStatus
from features.streaming.service import StreamingService
from repositories.stream_session_repository import StreamSessionRepository


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