"""
Tests for the creator dashboard analytics resolvers in api/graphql.py.

Covers:
- Auth/ownership requirements for creator_analytics and post_analytics
- Aggregation of views/watch time/completions/rewatches from InteractionSignal
- Follower growth, likes/shares (signal-based) and comments (join-based) totals
- Engagement rate calculation and top-post ranking
- Per-post analytics (avg watch time, completion rate)

Follows the pattern established in test_social_interactions.py: resolvers are
called directly with a lightweight AppContext, and repository methods are
monkeypatched so no real (Postgres-only) database is required.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, _creator_analytics, _creator_video_analytics, _post_analytics
from app.models.collaboration import CollaborationStatus
from app.models.analytics import SignalType
from services.creator_analytics_service import CreatorAnalyticsService


def make_ctx(user_id: uuid.UUID | None = None) -> AppContext:
    user = SimpleNamespace(id=user_id or uuid.uuid4())
    return AppContext(db=AsyncMock(), current_user=user)


def make_post(user_id, **overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=user_id,
        like_count=0,
        comment_count=0,
        share_count=0,
        view_count=0,
        content_type=None,
        status=None,
        title=None,
        body=None,
        caption=None,
        tags=None,
        mentions=None,
        sound_track=None,
        scheduled_at=None,
        published_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCreatorAnalytics:
    @pytest.mark.asyncio
    async def test_requires_auth(self):
        ctx = AppContext(db=AsyncMock(), current_user=None)
        period = SimpleNamespace(start=datetime.now(timezone.utc), end=datetime.now(timezone.utc))
        with pytest.raises(PermissionError):
            await _creator_analytics(ctx, period)

    @pytest.mark.asyncio
    async def test_creator_analytics_is_scoped_to_authenticated_creator(self, monkeypatch):
        creator_id = uuid.uuid4()
        ctx = AppContext(db=object(), current_user=SimpleNamespace(id=creator_id))
        period = SimpleNamespace(
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
        values = {
            "total_posts": 0, "total_uploads": 0, "total_published_videos": 0,
            "total_views": 0, "unique_viewers": 0, "total_likes": 0, "total_comments": 0,
            "total_shares": 0, "total_saves": 0, "new_followers": 0, "lost_followers": 0,
            "follower_growth": 0, "avg_watch_time": None, "completion_rate": None,
            "engagement_rate": 0.0, "views_growth_pct": None, "likes_growth_pct": None,
            "comments_growth_pct": None, "shares_growth_pct": None, "followers_growth_pct": None,
            "top_posts": [], "total_collaboration_requests": 0,
            "pending_collaborations": 0, "accepted_collaborations": 0,
            "declined_collaborations": 0, "cancelled_collaborations": 0,
            "active_collaborations": 0, "completed_collaborations": 0,
            "collaboration_acceptance_rate": None, "collaboration_completion_rate": None,
            "average_response_hours": None, "collaboration_success_rate": None,
            "total_collaboration_requests": 0, "pending_collaborations": 0,
            "accepted_collaborations": 0, "declined_collaborations": 0,
            "cancelled_collaborations": 0, "active_collaborations": 0,
            "completed_collaborations": 0,
        }

        async def fake_overview(self, requested_creator_id, start, end):
            assert requested_creator_id == creator_id
            assert start == period.start
            assert end == period.end
            return values

        monkeypatch.setattr("services.creator_analytics_service.CreatorAnalyticsService.overview", fake_overview)
        result = await _creator_analytics(ctx, period)
        assert result.total_collaboration_requests == 0

    @pytest.mark.asyncio
    async def test_graphql_response_contains_calculated_collaboration_metrics(self, monkeypatch):
        creator_id = uuid.uuid4()
        ctx = AppContext(db=object(), current_user=SimpleNamespace(id=creator_id))
        period = SimpleNamespace(
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
        values = {
            "total_posts": 0, "total_uploads": 0, "total_published_videos": 0,
            "total_views": 0, "unique_viewers": 0, "total_likes": 0, "total_comments": 0,
            "total_shares": 0, "total_saves": 0, "new_followers": 0, "lost_followers": 0,
            "follower_growth": 0, "avg_watch_time": None, "completion_rate": None,
            "engagement_rate": 0.0, "views_growth_pct": None, "likes_growth_pct": None,
            "comments_growth_pct": None, "shares_growth_pct": None, "followers_growth_pct": None,
            "top_posts": [], "total_collaboration_requests": 6,
            "pending_collaborations": 1, "accepted_collaborations": 3,
            "declined_collaborations": 1, "cancelled_collaborations": 1,
            "active_collaborations": 1, "completed_collaborations": 1,
            "collaboration_acceptance_rate": 50.0, "collaboration_completion_rate": 33.333,
            "average_response_hours": 2.0, "collaboration_success_rate": 33.333,
        }

        async def fake_overview(self, requested_creator_id, start, end):
            assert requested_creator_id == creator_id
            assert (start, end) == (period.start, period.end)
            return values

        monkeypatch.setattr("services.creator_analytics_service.CreatorAnalyticsService.overview", fake_overview)
        result = await _creator_analytics(ctx, period)

        assert result.total_collaboration_requests == 6
        assert result.pending_collaborations == 1
        assert result.accepted_collaborations == 3
        assert result.active_collaborations == 1
        assert result.completed_collaborations == 1
        assert result.collaboration_acceptance_rate == 50.0
        assert result.collaboration_completion_rate == pytest.approx(33.333)
        assert result.average_response_hours == 2.0

    @pytest.mark.asyncio
    async def test_collaboration_rates_are_none_with_zero_requests(self, monkeypatch):
        service = CreatorAnalyticsService(AsyncMock())

        async def fake_get_for_user_in_period(*args, **kwargs):
            return []

        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            fake_get_for_user_in_period,
        )
        values = await service._collaboration_totals(
            uuid.uuid4(), datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        assert values["collaboration_acceptance_rate"] is None
        assert values["collaboration_completion_rate"] is None
        assert values["average_response_hours"] is None

    @pytest.mark.asyncio
    async def test_aggregates_from_existing_data(self, monkeypatch):
        ctx = make_ctx()
        posts = [
            make_post(ctx.user.id, like_count=10, comment_count=2, share_count=1, view_count=100),
            make_post(ctx.user.id, like_count=1, comment_count=0, share_count=0, view_count=5),
        ]

        async def fake_get_by_user_id(self, user_id, limit=500):
            return posts

        signals = {
            SignalType.VIEW: {"count": 8, "total": 8.0},
            SignalType.REWATCH: {"count": 2, "total": 2.0},
            SignalType.LIKE: {"count": 11, "total": 11.0},
            SignalType.SHARE: {"count": 1, "total": 1.0},
            SignalType.WATCH_DURATION: {"count": 10, "total": 500.0},
            SignalType.COMPLETION: {"count": 4, "total": 4.0},
        }

        async def fake_signal_totals(self, *, creator_id=None, post_id=None, start=None, end=None):
            assert creator_id == ctx.user.id
            return signals

        async def fake_count_for_creator(self, creator_id, start=None, end=None):
            return 3

        async def fake_count_followers_since(self, user_id, start=None, end=None):
            return 7

        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id", fake_get_by_user_id
        )
        monkeypatch.setattr(
            "repositories.analytics_repository.AnalyticsRepository.signal_totals",
            fake_signal_totals,
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            fake_count_for_creator,
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            fake_count_followers_since,
        )

        period = SimpleNamespace(
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
        result = await _creator_analytics(ctx, period)

        assert result.total_posts == 2
        assert result.total_views == 10  # VIEW + REWATCH
        assert result.total_likes == 11
        assert result.total_shares == 1
        assert result.total_comments == 3
        assert result.follower_growth == 7
        assert result.new_followers == 7
        assert result.lost_followers == 0
        assert result.engagement_rate == pytest.approx((11 + 3 + 1) / 10 * 100)
        assert len(result.top_posts) == 2
        assert result.top_posts[0].likes == 10  # highest-engagement post first

    @pytest.mark.asyncio
    async def test_no_views_gives_zero_engagement_rate(self, monkeypatch):
        ctx = make_ctx()

        async def fake_get_by_user_id(self, user_id, limit=500):
            return []

        async def fake_signal_totals(self, **kwargs):
            return {}

        async def fake_count_for_creator(self, creator_id, start=None, end=None):
            return 0

        async def fake_count_followers_since(self, user_id, start=None, end=None):
            return 0

        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id", fake_get_by_user_id
        )
        monkeypatch.setattr(
            "repositories.analytics_repository.AnalyticsRepository.signal_totals",
            fake_signal_totals,
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            fake_count_for_creator,
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            fake_count_followers_since,
        )

        period = SimpleNamespace(start=datetime.now(timezone.utc), end=datetime.now(timezone.utc))
        result = await _creator_analytics(ctx, period)

        assert result.total_views == 0
        assert result.engagement_rate == 0.0
        assert result.top_posts == []

    @pytest.mark.asyncio
    async def test_net_follower_growth_and_unfollows(self, monkeypatch):
        ctx = make_ctx()
        start = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end = datetime(2026, 2, 28, tzinfo=timezone.utc)

        async def fake_get_by_user_id(self, user_id, limit=500):
            return []

        signals = {
            SignalType.VIEW: {"count": 10, "total": 10.0},
            SignalType.FOLLOW: {"count": 15, "total": 15.0},
            SignalType.UNFOLLOW: {"count": 3, "total": 3.0},
            SignalType.SAVE: {"count": 2, "total": 2.0},
            SignalType.UNSAVE: {"count": 1, "total": 1.0},
            SignalType.LIKE: {"count": 5, "total": 5.0},
            SignalType.UNLIKE: {"count": 1, "total": 1.0},
        }

        async def fake_signal_totals(self, *, creator_id=None, post_id=None, start=None, end=None):
            return signals

        async def fake_count_for_creator(self, creator_id, start=None, end=None):
            return 1

        async def fake_count_followers_since(self, user_id, start=None, end=None):
            return 15

        monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_user_id", fake_get_by_user_id)
        monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.signal_totals", fake_signal_totals)
        monkeypatch.setattr("repositories.content_repository.CommentRepository.count_for_creator", fake_count_for_creator)
        monkeypatch.setattr("repositories.social_repository.FollowRepository.count_followers_since", fake_count_followers_since)

        period = SimpleNamespace(start=start, end=end)
        result = await _creator_analytics(ctx, period)

        assert result.new_followers == 15
        assert result.lost_followers == 3
        assert result.follower_growth == 12
        assert result.total_likes == 4
        assert result.total_saves == 1

    @pytest.mark.asyncio
    async def test_creator_video_analytics_authorization(self):
        ctx = AppContext(db=AsyncMock(), current_user=None)
        period = SimpleNamespace(start=datetime.now(timezone.utc), end=datetime.now(timezone.utc))
        with pytest.raises(PermissionError):
            await _creator_video_analytics(ctx, period, "views")

    @pytest.mark.asyncio
    async def test_creator_analytics_service_overview_and_per_post(self, monkeypatch):
        creator_id = uuid.uuid4()
        post1 = make_post(creator_id, like_count=10, view_count=50)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 31, tzinfo=timezone.utc)

        db = AsyncMock()
        service = CreatorAnalyticsService(db)

        async def fake_posts(c_id):
            return [post1]

        signals = {
            SignalType.VIEW: {"count": 20, "total": 20.0},
            SignalType.REWATCH: {"count": 5, "total": 5.0},
            SignalType.WATCH_DURATION: {"count": 25, "total": 500.0},
            SignalType.COMPLETION: {"count": 10, "total": 10.0},
            SignalType.LIKE: {"count": 8, "total": 8.0},
            SignalType.SHARE: {"count": 2, "total": 2.0},
            SignalType.SAVE: {"count": 3, "total": 3.0},
            SignalType.FOLLOW: {"count": 5, "total": 5.0},
            SignalType.UNFOLLOW: {"count": 1, "total": 1.0},
        }

        async def fake_signal_totals(*args, **kwargs):
            return signals

        per_post = {
            post1.id: {
                SignalType.VIEW: {"count": 20, "total": 20.0},
                SignalType.REWATCH: {"count": 5, "total": 5.0},
                SignalType.WATCH_DURATION: {"count": 25, "total": 500.0},
                SignalType.COMPLETION: {"count": 10, "total": 10.0},
                SignalType.LIKE: {"count": 8, "total": 8.0},
                SignalType.SHARE: {"count": 2, "total": 2.0},
                SignalType.SAVE: {"count": 3, "total": 3.0},
            }
        }

        async def fake_per_post(*args, **kwargs):
            return per_post

        async def fake_count_for_creator(*args, **kwargs):
            return 0

        async def fake_collaboration_totals(*args, **kwargs):
            return {
                "total_collaboration_requests": 0,
                "pending_collaborations": 0,
                "accepted_collaborations": 0,
                "active_collaborations": 0,
                "completed_collaborations": 0,
                "collaboration_success_rate": None,
            }

        monkeypatch.setattr(service, "_posts", fake_posts)
        monkeypatch.setattr(service.analytics_repo, "signal_totals", fake_signal_totals)
        monkeypatch.setattr(service.analytics_repo, "per_post_signal_totals", fake_per_post)
        monkeypatch.setattr("repositories.content_repository.CommentRepository.count_for_creator", fake_count_for_creator)
        monkeypatch.setattr(service, "_collaboration_totals", fake_collaboration_totals)

        overview = await service.overview(creator_id, start, end)

        assert overview["total_posts"] == 1
        assert overview["total_views"] == 25
        assert overview["total_likes"] == 8
        assert overview["total_shares"] == 2
        assert overview["total_saves"] == 3
        assert overview["new_followers"] == 5
        assert overview["lost_followers"] == 1
        assert overview["follower_growth"] == 4
        assert overview["avg_watch_time"] == pytest.approx(500.0 / 25)
        assert overview["completion_rate"] == pytest.approx(10 / 25 * 100)
        assert len(overview["top_posts"]) == 1

    @pytest.mark.asyncio
    async def test_creator_service_compares_against_previous_period(self, monkeypatch):
        creator_id = uuid.uuid4()
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 8, tzinfo=timezone.utc)
        service = CreatorAnalyticsService(AsyncMock())

        async def fake_posts(c_id):
            return []

        signal_windows = [
            {
                SignalType.VIEW: {"count": 10, "total": 10.0},
                SignalType.LIKE: {"count": 5, "total": 5.0},
                SignalType.SHARE: {"count": 2, "total": 2.0},
                SignalType.FOLLOW: {"count": 3, "total": 3.0},
            },
            {},
        ]

        async def fake_signal_totals(*args, **kwargs):
            return signal_windows.pop(0)

        async def fake_count_for_creator(*args, **kwargs):
            return 4 if kwargs.get("start") == start else 0

        async def fake_collaboration_totals(*args, **kwargs):
            return {
                "total_collaboration_requests": 0,
                "pending_collaborations": 0,
                "accepted_collaborations": 0,
                "active_collaborations": 0,
                "completed_collaborations": 0,
                "collaboration_success_rate": None,
            }

        monkeypatch.setattr(service, "_posts", fake_posts)
        monkeypatch.setattr(service.analytics_repo, "signal_totals", fake_signal_totals)
        monkeypatch.setattr("repositories.content_repository.CommentRepository.count_for_creator", fake_count_for_creator)
        monkeypatch.setattr(service, "_collaboration_totals", fake_collaboration_totals)

        overview = await service.overview(creator_id, start, end)

        assert overview["views_growth_pct"] == 100.0
        assert overview["likes_growth_pct"] == 100.0
        assert overview["comments_growth_pct"] == 100.0
        assert overview["shares_growth_pct"] == 100.0
        assert overview["followers_growth_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_creator_service_collaboration_status_metrics(self, monkeypatch):
        creator_id = uuid.uuid4()
        service = CreatorAnalyticsService(AsyncMock())
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 31, tzinfo=timezone.utc)
        request_at = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)

        def collaboration(status, accepted_at=None, created_at=request_at):
            return SimpleNamespace(
                status=status,
                created_at=created_at,
                proposed_at=created_at.isoformat(),
                participants=[SimpleNamespace(accepted_at=accepted_at)],
            )

        async def fake_get_for_user_in_period(self, user_id, period_start, period_end):
            assert user_id == creator_id
            assert period_start == start
            assert period_end == end
            return [
                collaboration(CollaborationStatus.PROPOSED),
                collaboration(CollaborationStatus.ACCEPTED, (request_at + timedelta(hours=2)).isoformat()),
                collaboration(CollaborationStatus.IN_PROGRESS),
                collaboration(CollaborationStatus.COMPLETED),
                collaboration(CollaborationStatus.CANCELLED),
                collaboration(CollaborationStatus.DECLINED),
            ]

        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            fake_get_for_user_in_period,
        )

        values = await service._collaboration_totals(creator_id, start, end)

        assert values["total_collaboration_requests"] == 6
        assert values["pending_collaborations"] == 1
        assert values["accepted_collaborations"] == 3
        assert values["declined_collaborations"] == 1
        assert values["cancelled_collaborations"] == 1
        assert values["active_collaborations"] == 1
        assert values["completed_collaborations"] == 1
        assert values["collaboration_acceptance_rate"] == pytest.approx(50.0)
        assert values["collaboration_completion_rate"] == pytest.approx(33.333333)
        assert values["average_response_hours"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_collaboration_response_time_uses_collaboration_accepted_at(self, monkeypatch):
        service = CreatorAnalyticsService(AsyncMock())
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 31, tzinfo=timezone.utc)
        created_at = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)

        collaboration = SimpleNamespace(
            status=CollaborationStatus.ACCEPTED,
            created_at=created_at,
            accepted_at=(created_at + timedelta(hours=4)).isoformat(),
            participants=[],
        )

        async def fake_get_for_user_in_period(*args, **kwargs):
            return [collaboration]

        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            fake_get_for_user_in_period,
        )
        values = await service._collaboration_totals(uuid.uuid4(), start, end)

        assert values["total_collaboration_requests"] == 1
        assert values["accepted_collaborations"] == 1
        assert values["average_response_hours"] == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_creator_trends_fill_missing_days(self, monkeypatch):
        creator_id = uuid.uuid4()
        service = CreatorAnalyticsService(AsyncMock())
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        end = datetime(2026, 4, 3, tzinfo=timezone.utc)

        async def fake_daily_signal_totals(*args, **kwargs):
            return [{"date": "2026-04-02", "views": 5, "likes": 1, "comments": 0, "shares": 0, "saves": 0, "followers_gained": 1}]

        async def fake_get_for_user_in_period(*args, **kwargs):
            return []

        monkeypatch.setattr(service.analytics_repo, "daily_signal_totals", fake_daily_signal_totals)
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            fake_get_for_user_in_period,
        )

        rows = await service.daily_trends(creator_id, start, end)

        assert [row["date"] for row in rows] == ["2026-04-01", "2026-04-02", "2026-04-03"]
        assert rows[0]["views"] == 0
        assert rows[1]["views"] == 5
        assert rows[2]["followers_gained"] == 0
        assert rows[1]["collaborations_requested"] == 0

    @pytest.mark.asyncio
    async def test_creator_trends_include_period_collaboration_states(self, monkeypatch):
        creator_id = uuid.uuid4()
        service = CreatorAnalyticsService(AsyncMock())
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        end = datetime(2026, 4, 3, tzinfo=timezone.utc)

        async def fake_daily_signal_totals(*args, **kwargs):
            return []

        collaborations = [
            SimpleNamespace(created_at=datetime(2026, 4, 2, tzinfo=timezone.utc), status=CollaborationStatus.PROPOSED),
            SimpleNamespace(created_at=datetime(2026, 4, 2, tzinfo=timezone.utc), status=CollaborationStatus.IN_PROGRESS),
            SimpleNamespace(created_at=datetime(2026, 4, 2, tzinfo=timezone.utc), status=CollaborationStatus.COMPLETED),
            SimpleNamespace(created_at=datetime(2026, 4, 3, tzinfo=timezone.utc), status=CollaborationStatus.DECLINED),
        ]

        async def fake_get_for_user_in_period(self, user_id, period_start, period_end):
            assert user_id == creator_id
            assert period_start == start
            assert period_end == end
            return collaborations

        monkeypatch.setattr(service.analytics_repo, "daily_signal_totals", fake_daily_signal_totals)
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            fake_get_for_user_in_period,
        )
        rows = await service.daily_trends(creator_id, start, end)
        assert rows[1]["collaborations_requested"] == 3
        assert rows[1]["collaborations_pending"] == 1
        assert rows[1]["collaborations_accepted"] == 2
        assert rows[1]["collaborations_in_progress"] == 1
        assert rows[1]["collaborations_completed"] == 1
        assert rows[2]["collaborations_declined"] == 1


class TestPostAnalytics:
    @pytest.mark.asyncio
    async def test_requires_auth(self):
        ctx = AppContext(db=AsyncMock(), current_user=None)
        with pytest.raises(PermissionError):
            await _post_analytics(ctx, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_rejects_non_owner(self, monkeypatch):
        ctx = make_ctx()
        post = make_post(uuid.uuid4())  # owned by someone else

        async def fake_get_by_id(self, post_id):
            return post

        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_id", fake_get_by_id
        )

        with pytest.raises(PermissionError):
            await _post_analytics(ctx, post.id)

    @pytest.mark.asyncio
    async def test_returns_none_when_post_missing(self, monkeypatch):
        ctx = make_ctx()

        async def fake_get_by_id(self, post_id):
            return None

        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_id", fake_get_by_id
        )

        assert await _post_analytics(ctx, uuid.uuid4()) is None

    @pytest.mark.asyncio
    async def test_computes_watch_time_and_completion_rate(self, monkeypatch):
        ctx = make_ctx()
        post = make_post(
            ctx.user.id, like_count=5, comment_count=1, share_count=0, view_count=20
        )

        async def fake_get_by_id(self, post_id):
            return post

        signals = {
            SignalType.VIEW: {"count": 15, "total": 15.0},
            SignalType.REWATCH: {"count": 5, "total": 5.0},
            SignalType.WATCH_DURATION: {"count": 20, "total": 1000.0},
            SignalType.COMPLETION: {"count": 10, "total": 10.0},
        }

        async def fake_signal_totals(self, *, creator_id=None, post_id=None, start=None, end=None):
            assert post_id == post.id
            return signals

        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_id", fake_get_by_id
        )
        monkeypatch.setattr(
            "repositories.analytics_repository.AnalyticsRepository.signal_totals",
            fake_signal_totals,
        )

        result = await _post_analytics(ctx, post.id)

        assert result.views == 20
        assert result.likes == 5
        assert result.avg_watch_time == pytest.approx(1000.0 / 20)
        assert result.completion_rate == pytest.approx(10 / 20 * 100)

    @pytest.mark.asyncio
    async def test_no_watch_events_gives_none_rates(self, monkeypatch):
        ctx = make_ctx()
        post = make_post(ctx.user.id)

        async def fake_get_by_id(self, post_id):
            return post

        async def fake_signal_totals(self, **kwargs):
            return {}

        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_id", fake_get_by_id
        )
        monkeypatch.setattr(
            "repositories.analytics_repository.AnalyticsRepository.signal_totals",
            fake_signal_totals,
        )

        result = await _post_analytics(ctx, post.id)

        assert result.avg_watch_time is None
        assert result.completion_rate is None
