"""Authorization and response-contract tests for platform analytics."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, _platform_analytics
from app.models.analytics import EventType
from app.models.user import UserRole
from services.platform_analytics_service import PlatformAnalyticsService


def period():
    return SimpleNamespace(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 28, tzinfo=timezone.utc),
    )


def context(role: UserRole | None) -> AppContext:
    user = None if role is None else SimpleNamespace(id=uuid.uuid4(), role=role)
    return AppContext(db=AsyncMock(), current_user=user)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, UserRole.USER, UserRole.CREATOR])
async def test_platform_analytics_requires_admin(role):
    with pytest.raises(PermissionError, match="Admin access required|Authentication required"):
        await _platform_analytics(context(role), period())


@pytest.mark.asyncio
async def test_admin_can_read_aggregate_platform_analytics(monkeypatch):
    values = {
        "total_users": 2,
        "new_users": 1,
        "active_users": 1,
        "active_creators": 1,
        "daily_active_users": None,
        "weekly_active_users": None,
        "monthly_active_users": None,
        "total_uploads": 1,
        "total_published_videos": 1,
        "total_views": 10,
        "unique_viewers": 2,
        "total_likes": 3,
        "total_comments": 1,
        "total_shares": 1,
        "total_saves": 1,
        "profile_views": 0,
        "sounds_used": 0,
        "collabs_created": 0,
        "follows_created": 1,
        "follows_removed": 0,
        "net_followers": 1,
        "average_views_per_published_video": 10.0,
        "engagement_rate": 60.0,
        "average_watch_time": 3.0,
        "completion_rate": 50.0,
        "feed_impressions": 4,
        "video_completions": 5,
        "video_skips": 1,
        "searches": 0,
        "searchers": 0,
        "notifications_generated": None,
        "notifications_opened": 0,
        "notification_open_rate": None,
    }

    async def fake_overview(self, start, end):
        return values

    monkeypatch.setattr(
        "services.platform_analytics_service.PlatformAnalyticsService.overview",
        fake_overview,
    )
    result = await _platform_analytics(context(UserRole.ADMIN), period())

    assert result.total_users == 2
    assert result.total_views == 10
    assert result.engagement_rate == 60.0
    assert not hasattr(result, "events")


@pytest.mark.asyncio
async def test_overview_aggregates_events_and_handles_watch_metrics():
    class Result:
        def __init__(self, scalar=None, rows=()):
            self.scalar = scalar
            self.rows = rows

        def scalar_one(self):
            return self.scalar

        def all(self):
            return self.rows

    event_rows = [
        SimpleNamespace(event_type=EventType.VIDEO_VIEWED, count=10, unique_users=3, duration_ms=0),
        SimpleNamespace(event_type=EventType.VIDEO_WATCHED, count=10, unique_users=2, duration_ms=30000),
        SimpleNamespace(event_type=EventType.VIDEO_COMPLETED, count=5, unique_users=2, duration_ms=0),
        SimpleNamespace(event_type=EventType.LIKE_CREATED, count=2, unique_users=2, duration_ms=0),
        SimpleNamespace(event_type=EventType.COMMENT_CREATED, count=1, unique_users=1, duration_ms=0),
        SimpleNamespace(event_type=EventType.SHARE_CREATED, count=1, unique_users=1, duration_ms=0),
        SimpleNamespace(event_type=EventType.SAVE_CREATED, count=1, unique_users=1, duration_ms=0),
        SimpleNamespace(event_type=EventType.VIDEO_UPLOADED, count=3, unique_users=2, duration_ms=0),
        SimpleNamespace(event_type=EventType.VIDEO_PUBLISHED, count=2, unique_users=2, duration_ms=0),
    ]
    db = AsyncMock()
    db.execute.side_effect = [
        Result(4), Result(2), Result(3), Result(1),
        Result(3), Result(1), Result(2), Result(3), Result(4),
        Result(rows=event_rows), Result(rows=[]), Result(1), Result(0), Result(2),
        Result(rows=[SimpleNamespace(moderation_status="approved", count=6)]),
    ]
    values = await PlatformAnalyticsService(db).overview(period().start, period().end)

    assert values["total_users"] == 4
    assert values["total_creators"] == 3
    assert values["new_creators"] == 1
    assert values["new_users"] == 2
    assert values["active_users"] == 3
    assert values["active_creators"] == 1
    assert values["daily_active_users"] == 2
    assert values["weekly_active_users"] == 3
    assert values["monthly_active_users"] == 4
    assert values["total_views"] == 10
    assert values["unique_viewers"] == 3
    assert values["total_uploads"] == 3
    assert values["total_published_videos"] == 2
    assert values["average_watch_time"] == 3.0
    assert values["completion_rate"] == 50.0
    assert values["engagement_rate"] == 50.0
    assert values["approved_content"] == 6
    assert values["comparison"]["views_growth_pct"] == 100.0


@pytest.mark.asyncio
async def test_overview_zero_views_returns_safe_rates():
    class Result:
        def __init__(self, scalar=None, rows=()):
            self.scalar = scalar
            self.rows = rows

        def scalar_one(self):
            return self.scalar

        def all(self):
            return self.rows

    db = AsyncMock()
    db.execute.side_effect = [
        Result(0), Result(0), Result(0), Result(0), Result(0),
        Result(0), Result(0), Result(0), Result(0),
        Result(rows=[]), Result(rows=[]), Result(0), Result(0),
        Result(rows=[]), Result(0),
    ]
    values = await PlatformAnalyticsService(db).overview(period().start, period().end)

    assert values["engagement_rate"] == 0.0
    assert values["average_watch_time"] is None
    assert values["completion_rate"] is None


@pytest.mark.asyncio
async def test_platform_trends_fill_missing_days():
    class Result:
        def all(self):
            return [SimpleNamespace(day="2026-01-02", event_type=EventType.VIDEO_VIEWED, count=7)]

    db = AsyncMock()
    db.execute.return_value = Result()
    rows = await PlatformAnalyticsService(db).daily_trends(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    assert [row["date"] for row in rows] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert rows[0]["views"] == 0
    assert rows[1]["views"] == 7
    assert rows[2]["engagement"] == 0


@pytest.mark.asyncio
async def test_top_content_supports_completion_and_engagement_sort_aliases():
    class Result:
        def __init__(self, rows=()):
            self.rows = rows

        def all(self):
            return self.rows

    post_a = SimpleNamespace(id=uuid.uuid4(), title="A", caption="A", thumbnail=None, media_url=None, published_at=None)
    post_b = SimpleNamespace(id=uuid.uuid4(), title="B", caption="B", thumbnail=None, media_url=None, published_at=None)

    def build_rows(post, views, likes, comments, shares, saves, completed):
        return [
            SimpleNamespace(post_id=post.id, event_type=EventType.VIDEO_VIEWED, count=views, unique_users=views, duration_ms=0),
            SimpleNamespace(post_id=post.id, event_type=EventType.LIKE_CREATED, count=likes, unique_users=likes, duration_ms=0),
            SimpleNamespace(post_id=post.id, event_type=EventType.COMMENT_CREATED, count=comments, unique_users=comments, duration_ms=0),
            SimpleNamespace(post_id=post.id, event_type=EventType.SHARE_CREATED, count=shares, unique_users=shares, duration_ms=0),
            SimpleNamespace(post_id=post.id, event_type=EventType.SAVE_CREATED, count=saves, unique_users=saves, duration_ms=0),
            SimpleNamespace(post_id=post.id, event_type=EventType.VIDEO_COMPLETED, count=completed, unique_users=completed, duration_ms=0),
        ]

    low = build_rows(post_a, views=10, likes=1, comments=0, shares=0, saves=0, completed=2)
    high = build_rows(post_b, views=20, likes=5, comments=2, shares=1, saves=1, completed=8)

    db = AsyncMock()
    db.execute.side_effect = [
        Result(rows=low + high),
        Result(rows=[post_a, post_b]),
        Result(rows=low + high),
        Result(rows=[post_a, post_b]),
    ]

    completion_rows = await PlatformAnalyticsService(db).top_content(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 28, tzinfo=timezone.utc),
        "completionRate",
        10,
    )
    assert completion_rows[0]["post"].id == post_b.id
    assert completion_rows[0]["completion_rate"] == pytest.approx(40.0)

    engagement_rows = await PlatformAnalyticsService(db).top_content(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 28, tzinfo=timezone.utc),
        "engagementRate",
        10,
    )
    assert engagement_rows[0]["post"].id == post_b.id
    assert engagement_rows[0]["engagement_rate"] == pytest.approx(45.0)