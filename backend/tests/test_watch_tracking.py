"""Regression tests for watch and engagement tracking resolvers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, _track_post_watch


def make_ctx(user_id: uuid.UUID | None = None) -> AppContext:
    user = SimpleNamespace(id=user_id or uuid.uuid4())
    return AppContext(db=AsyncMock(), current_user=user)


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch):
    async def noop_record(self, **kwargs):
        return None

    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.record", noop_record)
    monkeypatch.setattr("services.analytics_event_service.AnalyticsEventService.track_event", noop_record)


@pytest.fixture
def watch_store(monkeypatch):
    events: list[SimpleNamespace] = []

    async def fake_get_by_id(self, post_id):
        return SimpleNamespace(id=post_id, user_id=uuid.uuid4(), duration_sec=100.0, view_count=0)

    async def fake_track_watch(self, post_id, user_id, watched_seconds, completed):
        event = SimpleNamespace(
            post_id=post_id,
            user_id=user_id,
            watched_seconds=watched_seconds,
            completed=completed,
            rewatched=any(event.post_id == post_id and event.user_id == user_id for event in events),
        )
        events.append(event)
        return event

    async def fake_count_views(self, post_id):
        return len({event.user_id for event in events if event.post_id == post_id})

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.track_watch", fake_track_watch)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_views", fake_count_views)

    return events


@pytest.mark.asyncio
async def test_first_watch_records_view_and_completion(watch_store):
    ctx = make_ctx()
    post_id = uuid.uuid4()

    result = await _track_post_watch(ctx, post_id, watched_seconds=95.0, completed=True)

    assert result.views == 1
    assert result.watched_seconds == 95.0
    assert result.completed is True
    assert result.rewatched is False
    assert watch_store == [
        SimpleNamespace(
            post_id=post_id,
            user_id=ctx.user.id,
            watched_seconds=95.0,
            completed=True,
            rewatched=False,
        )
    ]
    ctx.db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_watch_time_accumulates_without_overwriting_events(watch_store):
    ctx = make_ctx()
    post_id = uuid.uuid4()

    first = await _track_post_watch(ctx, post_id, watched_seconds=15.0, completed=False)
    second = await _track_post_watch(ctx, post_id, watched_seconds=20.0, completed=False)

    assert first.views == 1
    assert second.views == 1
    assert [event.watched_seconds for event in watch_store] == [15.0, 20.0]
    assert sum(event.watched_seconds for event in watch_store) == 35.0
    assert len(watch_store) == 2


@pytest.mark.asyncio
async def test_completion_is_verified_against_watch_duration(watch_store):
    ctx = make_ctx()
    post_id = uuid.uuid4()

    too_short = await _track_post_watch(ctx, post_id, watched_seconds=89.0, completed=True)
    completed = await _track_post_watch(ctx, post_id, watched_seconds=90.0, completed=True)

    assert too_short.completed is False
    assert completed.completed is True
    assert [event.completed for event in watch_store] == [False, True]


@pytest.mark.asyncio
async def test_watch_seconds_are_clamped_to_post_duration(watch_store):
    ctx = make_ctx()
    post_id = uuid.uuid4()

    result = await _track_post_watch(ctx, post_id, watched_seconds=600.0, completed=True)

    assert result.watched_seconds == 100.0
    assert result.completed is True
    assert watch_store[0].watched_seconds == 100.0


@pytest.mark.asyncio
async def test_rewatch_detected_and_additional_viewer_increments_views(watch_store):
    post_id = uuid.uuid4()
    first_ctx = make_ctx()
    second_ctx = make_ctx()

    first = await _track_post_watch(first_ctx, post_id, watched_seconds=12.0, completed=False)
    rewatch = await _track_post_watch(first_ctx, post_id, watched_seconds=8.0, completed=False)
    other_viewer = await _track_post_watch(second_ctx, post_id, watched_seconds=30.0, completed=False)

    assert first.views == 1
    assert first.rewatched is False
    assert rewatch.views == 1
    assert rewatch.rewatched is True
    assert other_viewer.views == 2
    assert other_viewer.rewatched is False
    assert len(watch_store) == 3