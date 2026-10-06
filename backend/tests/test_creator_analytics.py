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
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.graphql import AppContext, _creator_analytics, _creator_video_analytics, _post_analytics, schema
from app.models.collaboration import CollaborationStatus
from app.models.analytics import EventType, SignalType
from app.models.streaming import StreamPlatform, StreamSessionStatus
from repositories.analytics_repository import AnalyticsRepository
from repositories.analytics_event_repository import AnalyticsEventRepository
from repositories.stream_session_repository import StreamSessionRepository
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signal_type",
    [SignalType.SHARE, SignalType.LIKE, SignalType.SAVE, SignalType.NOT_INTERESTED],
)
@pytest.mark.parametrize("actor_count", [4, 0])
async def test_unique_signal_actor_aggregate_counts_distinct_users(signal_type, actor_count):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one=lambda: actor_count)
    repository = AnalyticsRepository(db)
    creator_id = uuid.uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 8, tzinfo=timezone.utc)

    result = await repository.unique_signal_actor_count(
        creator_id=creator_id,
        signal_type=signal_type,
        start=start,
        end=end,
    )

    assert result == actor_count
    query = db.execute.await_args.args[0]
    statement = str(query)
    assert "count(distinct(interaction_signals.user_id))" in statement
    assert "interaction_signals.creator_id =" in statement
    assert "interaction_signals.signal_type =" in statement
    assert "interaction_signals.created_at >=" in statement
    assert "interaction_signals.created_at <=" in statement
    assert query.compile().params == {
        "creator_id_1": creator_id,
        "signal_type_1": signal_type,
        "created_at_1": start,
        "created_at_2": end,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows, expected",
    [
        (
            [
                SimpleNamespace(signal_type=SignalType.UNLIKE, actors=2),
                SimpleNamespace(signal_type=SignalType.REWATCH, actors=4),
            ],
            {SignalType.UNLIKE: 2, SignalType.REWATCH: 4},
        ),
        ([], {}),
    ],
)
async def test_unique_signal_actor_counts_groups_by_signal_type(rows, expected):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: rows)
    repository = AnalyticsRepository(db)
    creator_id = uuid.uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 8, tzinfo=timezone.utc)

    result = await repository.unique_signal_actor_counts(
        creator_id=creator_id,
        signal_types=[SignalType.UNLIKE, SignalType.REWATCH],
        start=start,
        end=end,
    )

    assert result == expected
    query = db.execute.await_args.args[0]
    statement = str(query)
    assert "count(distinct(interaction_signals.user_id))" in statement
    assert "interaction_signals.creator_id =" in statement
    assert "interaction_signals.signal_type IN" in statement
    assert "interaction_signals.created_at >=" in statement
    assert "interaction_signals.created_at <=" in statement
    params = query.compile().params
    assert creator_id in params.values()
    assert start in params.values()
    assert end in params.values()


@pytest.mark.asyncio
async def test_unique_commenters_aggregate_counts_distinct_users():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one=lambda: 3)
    from repositories.content_repository import CommentRepository

    repository = CommentRepository(db)
    result = await repository.count_unique_commenters_for_creator(
        uuid.uuid4(),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert result == 3
    statement = str(db.execute.await_args.args[0])
    assert "count(distinct(comments.user_id))" in statement
    assert "JOIN posts ON comments.post_id = posts.id" in statement


@pytest.mark.asyncio
async def test_stream_repository_limits_duration_input_to_ended_sessions():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.scalars.return_value.all.return_value = []
    creator_id = uuid.uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)

    await StreamSessionRepository(db).get_ended_for_owner_in_period(
        creator_id, start, end
    )

    statement = db.execute.await_args.args[0]
    sql = str(statement)
    params = statement.compile().params
    assert "stream_sessions.owner_id =" in sql
    assert "stream_sessions.status =" in sql
    assert "stream_sessions.started_at IS NOT NULL" in sql
    assert "stream_sessions.ended_at IS NOT NULL" in sql
    assert "stream_sessions.ended_at >=" in sql
    assert "stream_sessions.ended_at <=" in sql
    assert creator_id in params.values()
    assert StreamSessionStatus.ENDED in params.values()
    assert start in params.values()
    assert end in params.values()


class TestCreatorAnalytics:
    @pytest.fixture(autouse=True)
    def stub_unique_viewer_counts(self, monkeypatch):
        async def empty_counts(self, *, creator_id, post_ids=None, start=None, end=None):
            return {"total": 0, "by_post": {}}

        monkeypatch.setattr(AnalyticsRepository, "unique_viewer_counts", empty_counts)
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_signal_actor_count",
            AsyncMock(return_value=0),
        )

        async def empty_actor_counts(
            self, *, creator_id, signal_types, start, end
        ):
            return {}

        monkeypatch.setattr(
            AnalyticsRepository, "unique_signal_actor_counts", empty_actor_counts
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_unique_commenters_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.counts_by_post",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            AnalyticsEventRepository,
            "profile_viewer_counts_for_creator",
            AsyncMock(return_value={"total": 0, "unique_viewers": 0}),
        )
        monkeypatch.setattr(
            AnalyticsEventRepository,
            "post_event_totals_for_creator",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            StreamSessionRepository,
            "get_ended_for_owner_in_period",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            StreamSessionRepository,
            "ended_destination_counts_for_owner_in_period",
            AsyncMock(return_value=[]),
        )

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
            "unique_commenters": 0,
            "like_rate": None, "comment_rate": None,
            "total_shares": 0, "unique_sharers": 0,
            "total_saves": 0, "profile_views": 0,
            "unique_profile_viewers": 0, "unique_profile_viewer_rate": None,
            "profile_views_growth_pct": None, "feed_impressions": 0,
            "unique_impression_viewers": 0,
            "feed_impressions_growth_pct": None, "video_skips": 0,
            "video_skip_rate": None, "current_followers": 0,
            "new_followers": 0, "lost_followers": 0,
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
            "unique_commenters": 0,
            "like_rate": None, "comment_rate": None,
            "total_shares": 0, "unique_sharers": 0,
            "total_saves": 0, "profile_views": 0,
            "unique_profile_viewers": 0, "unique_profile_viewer_rate": None,
            "profile_views_growth_pct": None, "feed_impressions": 0,
            "unique_impression_viewers": 0,
            "feed_impressions_growth_pct": None, "video_skips": 0,
            "video_skip_rate": None, "current_followers": 0,
            "new_followers": 0, "lost_followers": 0,
            "follower_growth": 0, "avg_watch_time": None, "completion_rate": None,
            "engagement_rate": 0.0, "views_growth_pct": None, "likes_growth_pct": None,
            "comments_growth_pct": None, "shares_growth_pct": None, "followers_growth_pct": None,
            "top_posts": [], "total_collaboration_requests": 6,
            "pending_collaborations": 1, "accepted_collaborations": 3,
            "declined_collaborations": 1, "cancelled_collaborations": 1,
            "active_collaborations": 1, "completed_collaborations": 1,
            "collaboration_acceptance_rate": 50.0, "collaboration_completion_rate": 33.333,
            "average_response_hours": 2.0, "collaboration_success_rate": 33.333,
            "total_broadcast_duration": 5400.0,
        }

        async def fake_overview(self, requested_creator_id, start, end):
            assert requested_creator_id == creator_id
            assert (start, end) == (period.start, period.end)
            return values

        monkeypatch.setattr("services.creator_analytics_service.CreatorAnalyticsService.overview", fake_overview)
        result = await _creator_analytics(ctx, period)

        assert result.total_broadcast_duration == pytest.approx(5400.0)
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
            SignalType.NOT_INTERESTED: {"count": 3, "total": 3.0},
        }

        async def fake_signal_totals(self, *, creator_id=None, post_id=None, start=None, end=None):
            assert creator_id == ctx.user.id
            return signals

        async def fake_unique_viewer_counts(
            self, *, creator_id, post_ids=None, start=None, end=None
        ):
            if post_ids is not None:
                return {"total": 6, "by_post": {posts[0].id: 7, posts[1].id: 2}}
            return {"total": 6, "by_post": {}}

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
        monkeypatch.setattr(AnalyticsRepository, "unique_viewer_counts", fake_unique_viewer_counts)
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
        assert result.total_rewatches == 2
        assert result.rewatch_rate == pytest.approx(20.0)
        assert result.unique_viewers == 6
        assert result.total_likes == 11
        assert result.total_shares == 1
        assert result.total_comments == 3
        assert result.follower_growth == 7
        assert result.new_followers == 7
        assert result.lost_followers == 0
        assert result.total_watch_time == 500.0
        assert result.total_not_interested == 3
        assert result.engagement_rate == pytest.approx((11 + 3 + 1) / 10 * 100)
        assert len(result.top_posts) == 2
        assert result.top_posts[0].likes == 10  # highest-engagement post first
        assert result.top_posts[0].unique_viewers == 7

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
        assert result.total_rewatches == 0
        assert result.rewatch_rate is None
        assert result.save_rate is None
        assert result.total_watch_time == 0.0
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
            SignalType.NOT_INTERESTED: {"count": 2, "total": 2.0},
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
        assert result.total_not_interested == 2
        assert result.save_rate == pytest.approx(10.0)

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
            SignalType.NOT_INTERESTED: {"count": 2, "total": 2.0},
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
        assert overview["total_rewatches"] == 5
        assert overview["rewatch_rate"] == pytest.approx(20.0)
        assert overview["total_likes"] == 8
        assert overview["total_shares"] == 2
        assert overview["share_rate"] == pytest.approx(8.0)
        assert overview["total_saves"] == 3
        assert overview["save_rate"] == pytest.approx(12.0)
        assert overview["new_followers"] == 5
        assert overview["lost_followers"] == 1
        assert overview["follower_growth"] == 4
        assert overview["total_watch_time"] == 500.0
        assert overview["total_broadcast_duration"] == 0.0
        assert overview["total_not_interested"] == 2
        assert overview["avg_watch_time"] == pytest.approx(500.0 / 25)
        assert overview["completion_rate"] == pytest.approx(10 / 25 * 100)
        assert len(overview["top_posts"]) == 1

    @pytest.mark.asyncio
    async def test_broadcast_duration_for_ended_stream(self, monkeypatch):
        creator_id = uuid.uuid4()
        started_at = datetime(2026, 1, 3, 10, tzinfo=timezone.utc)
        ended_at = datetime(2026, 1, 3, 11, 30, tzinfo=timezone.utc)

        async def ended_sessions(self, requested_creator_id, start, end):
            assert requested_creator_id == creator_id
            return [SimpleNamespace(started_at=started_at, ended_at=ended_at)]

        monkeypatch.setattr(
            StreamSessionRepository, "get_ended_for_owner_in_period", ended_sessions
        )
        duration = await CreatorAnalyticsService(AsyncMock())._total_broadcast_duration(
            creator_id,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        )

        assert duration == pytest.approx(5400.0)

    @pytest.mark.asyncio
    async def test_broadcast_duration_sums_multiple_streams(self, monkeypatch):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sessions = [
            SimpleNamespace(
                started_at=datetime(2026, 1, 3, 10, tzinfo=timezone.utc),
                ended_at=datetime(2026, 1, 3, 10, 45, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                started_at=datetime(2026, 1, 8, 14, tzinfo=timezone.utc),
                ended_at=datetime(2026, 1, 8, 15, 15, tzinfo=timezone.utc),
            ),
        ]

        async def ended_sessions(self, requested_creator_id, period_start, period_end):
            assert requested_creator_id == creator_id
            return sessions

        monkeypatch.setattr(
            StreamSessionRepository, "get_ended_for_owner_in_period", ended_sessions
        )
        duration = await CreatorAnalyticsService(AsyncMock())._total_broadcast_duration(
            creator_id, start, datetime(2026, 1, 31, tzinfo=timezone.utc)
        )

        assert duration == pytest.approx(7200.0)

    @pytest.mark.asyncio
    async def test_broadcast_duration_ignores_stream_without_end_timestamp(self, monkeypatch):
        creator_id = uuid.uuid4()

        async def ended_sessions(self, requested_creator_id, start, end):
            return [
                SimpleNamespace(
                    started_at=datetime(2026, 1, 3, 10, tzinfo=timezone.utc),
                    ended_at=None,
                )
            ]

        monkeypatch.setattr(
            StreamSessionRepository, "get_ended_for_owner_in_period", ended_sessions
        )
        duration = await CreatorAnalyticsService(AsyncMock())._total_broadcast_duration(
            creator_id,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        )

        assert duration == 0.0

    @pytest.mark.asyncio
    async def test_broadcast_duration_is_zero_when_creator_has_no_streams(self):
        duration = await CreatorAnalyticsService(AsyncMock())._total_broadcast_duration(
            uuid.uuid4(),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        )

        assert duration == 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("session_count", [0, 1, 3])
    async def test_graphql_exposes_ended_stream_totals(self, monkeypatch, session_count):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        sessions = [
            SimpleNamespace(
                started_at=start + timedelta(days=index),
                ended_at=start + timedelta(days=index, hours=1),
            )
            for index in range(session_count)
        ]
        ended_sessions = AsyncMock(return_value=sessions)
        breakdown = (
            [{"platform": StreamPlatform.TWITCH, "ended_sessions": session_count}]
            if session_count else []
        )
        destination_counts = AsyncMock(return_value=breakdown)
        monkeypatch.setattr(
            StreamSessionRepository,
            "ended_destination_counts_for_owner_in_period",
            destination_counts,
        )
        monkeypatch.setattr(
            StreamSessionRepository, "get_ended_for_owner_in_period", ended_sessions
        )
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { totalEndedStreamSessions totalBroadcastDuration
                streamDestinationBreakdown { platform endedSessions } } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {
            "creatorAnalytics": {
                "totalEndedStreamSessions": session_count,
                "totalBroadcastDuration": session_count * 3600.0,
                "streamDestinationBreakdown": (
                    [{"platform": "twitch", "endedSessions": session_count}]
                    if session_count else []
                ),
            }
        }
        ended_sessions.assert_awaited_once_with(creator_id, start, end)
        destination_counts.assert_awaited_once_with(creator_id, start, end)

    @pytest.mark.asyncio
    async def test_stream_totals_exclude_invalid_duration_inputs(self, monkeypatch):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        monkeypatch.setattr(
            StreamSessionRepository, "get_ended_for_owner_in_period",
            AsyncMock(return_value=[
                SimpleNamespace(started_at=start, ended_at=None),
                SimpleNamespace(started_at=None, ended_at=start),
                SimpleNamespace(started_at=start, ended_at=start - timedelta(seconds=1)),
                SimpleNamespace(started_at=start, ended_at=start),
            ]),
        )

        assert await CreatorAnalyticsService(AsyncMock())._stream_session_totals(
            uuid.uuid4(), start, start + timedelta(days=1)
        ) == (1, 0.0)

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
    async def test_profile_views_are_scoped_to_period_and_growth(self, monkeypatch):
        creator_id = uuid.uuid4()
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        end = datetime(2026, 5, 8, tzinfo=timezone.utc)
        service = CreatorAnalyticsService(AsyncMock())

        monkeypatch.setattr(service, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(service.analytics_repo, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            service.analytics_repo,
            "unique_viewer_counts",
            AsyncMock(return_value={"total": 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        async def profile_viewer_counts(self, requested_creator_id, period_start, period_end):
            assert requested_creator_id == creator_id
            return {
                "total": 12 if period_start == start else 8,
                "unique_viewers": 9 if period_start == start else 7,
            }

        async def post_event_totals(self, requested_creator_id, period_start, period_end):
            assert requested_creator_id == creator_id
            return {
                EventType.VIDEO_VIEWED: {"count": 10, "unique_users": 8},
                EventType.VIDEO_IMPRESSION: {
                    "count": 120 if period_start == start else 80,
                    "unique_users": 45 if period_start == start else 30,
                },
                EventType.VIDEO_SKIPPED: {"count": 2, "unique_users": 2},
            }

        monkeypatch.setattr(
            AnalyticsEventRepository,
            "profile_viewer_counts_for_creator",
            profile_viewer_counts,
        )
        monkeypatch.setattr(
            AnalyticsEventRepository,
            "post_event_totals_for_creator",
            post_event_totals,
        )

        values = await service.overview(creator_id, start, end)

        assert values["profile_views"] == 12
        assert values["unique_profile_viewers"] == 9
        assert values["profile_views_growth_pct"] == 50.0
        assert values["feed_impressions"] == 120
        assert values["unique_impression_viewers"] == 45
        assert values["feed_impressions_growth_pct"] == 50.0
        assert values["video_skips"] == 2
        assert values["video_skip_rate"] == 20.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "profile_view_counts, expected_rate",
        [({"total": 5, "unique_viewers": 3}, 60.0), ({"total": 0, "unique_viewers": 0}, None)],
    )
    async def test_graphql_exposes_profile_view_metrics(
        self, monkeypatch, profile_view_counts, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)

        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_viewer_counts",
            AsyncMock(return_value={"total": 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            AnalyticsEventRepository,
            "profile_viewer_counts_for_creator",
            AsyncMock(side_effect=[
                profile_view_counts,
                {"total": 0, "unique_viewers": 0},
            ]),
        )
        monkeypatch.setattr(
            AnalyticsEventRepository,
            "post_event_totals_for_creator",
            AsyncMock(side_effect=[
                {
                    EventType.VIDEO_IMPRESSION: {"count": 50, "unique_users": 30},
                    EventType.VIDEO_VIEWED: {"count": 100, "unique_users": 80},
                    EventType.VIDEO_SKIPPED: {"count": 25, "unique_users": 25},
                },
                {},
            ]),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { profileViews uniqueProfileViewers uniqueProfileViewerRate profileViewsGrowthPct feedImpressions uniqueImpressionViewers feedImpressionsGrowthPct videoSkips videoSkipRate } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {
            "creatorAnalytics": {
                "profileViews": profile_view_counts["total"],
                "uniqueProfileViewers": profile_view_counts["unique_viewers"],
                "uniqueProfileViewerRate": expected_rate,
                "profileViewsGrowthPct": 100.0 if profile_view_counts["total"] else None,
                "feedImpressions": 50,
                "uniqueImpressionViewers": 30,
                "feedImpressionsGrowthPct": 100.0,
                "videoSkips": 25,
                "videoSkipRate": 25.0,
            }
        }

    @pytest.mark.parametrize(
        "profile_view_counts, expected_rate",
        [({"total": 5, "unique_viewers": 3}, 60.0), ({"total": 0, "unique_viewers": 0}, None)],
    )
    @pytest.mark.asyncio
    async def test_legacy_graphql_exposes_profile_view_metrics(
        self, monkeypatch, profile_view_counts, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_viewer_counts",
            AsyncMock(return_value={"total": 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            AnalyticsEventRepository,
            "profile_viewer_counts_for_creator",
            AsyncMock(return_value=profile_view_counts),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { profileViews uniqueProfileViewers uniqueProfileViewerRate } }''',
            context_value=AppContext(
                db=AsyncMock(), current_user=SimpleNamespace(id=creator_id)
            ),
        )

        assert result.errors is None
        assert result.data == {
            "creatorAnalytics": {
                "profileViews": profile_view_counts["total"],
                "uniqueProfileViewers": profile_view_counts["unique_viewers"],
                "uniqueProfileViewerRate": expected_rate,
            }
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "has_data, expected_views, expected_viewers, expected_rate",
        [(True, 10, 3, 30.0), (False, 0, 0, None)],
    )
    async def test_graphql_exposes_unique_viewer_rate_from_service(
        self, monkeypatch, has_data, expected_views, expected_viewers, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)

        async def signal_totals(self, **kwargs):
            if kwargs["start"] == start and has_data:
                return {
                    SignalType.VIEW: {"count": 8, "total": 8.0},
                    SignalType.REWATCH: {"count": 2, "total": 2.0},
                }
            return {}

        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", signal_totals)
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_viewer_counts",
            AsyncMock(side_effect=[
                {"total": expected_viewers, "by_post": {}},
                {"total": 0, "by_post": {}},
            ]),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { totalViews uniqueViewers uniqueViewerRate } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "totalViews": expected_views,
            "uniqueViewers": expected_viewers,
            "uniqueViewerRate": expected_rate,
        }}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "has_data, expected_views, expected_viewers, expected_rate",
        [(True, 10, 3, 30.0), (False, 0, 0, None)],
    )
    async def test_legacy_graphql_exposes_unique_viewer_rate(
        self, monkeypatch, has_data, expected_views, expected_viewers, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )

        async def signal_totals(self, **kwargs):
            if has_data:
                return {SignalType.VIEW: {"count": 8, "total": 8.0},
                        SignalType.REWATCH: {"count": 2, "total": 2.0}}
            return {}

        monkeypatch.setattr(AnalyticsRepository, "signal_totals", signal_totals)
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_viewer_counts",
            AsyncMock(return_value={"total": expected_viewers, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { totalViews uniqueViewers uniqueViewerRate } }''',
            context_value=AppContext(
                db=AsyncMock(), current_user=SimpleNamespace(id=creator_id)
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "totalViews": expected_views,
            "uniqueViewers": expected_viewers,
            "uniqueViewerRate": expected_rate,
        }}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("current_followers", [42, 0])
    async def test_graphql_exposes_current_followers_from_service(
        self, monkeypatch, current_followers
    ):
        creator_id = uuid.uuid4()
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers",
            AsyncMock(return_value=current_followers),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { currentFollowers } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {"currentFollowers": current_followers}}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("current_followers", [42, 0])
    async def test_legacy_graphql_exposes_current_followers(
        self, monkeypatch, current_followers
    ):
        creator_id = uuid.uuid4()
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers",
            AsyncMock(return_value=current_followers),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { currentFollowers } }''',
            context_value=AppContext(
                db=AsyncMock(), current_user=SimpleNamespace(id=creator_id)
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {"currentFollowers": current_followers}}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("unique_likers", [4, 0])
    async def test_graphql_exposes_unique_likers(
        self, monkeypatch, legacy, unique_likers
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            AnalyticsRepository, "signal_totals",
            AsyncMock(return_value={
                SignalType.LIKE: {"count": 9, "total": 9.0},
                SignalType.UNLIKE: {"count": 9, "total": 9.0},
            } if unique_likers else {}),
        )

        async def unique_actor_count(self, **kwargs):
            assert kwargs["creator_id"] == creator_id
            assert kwargs["start"] == start
            assert kwargs["end"] == end
            return unique_likers if kwargs["signal_type"] == SignalType.LIKE else 0

        actor_count = AsyncMock(side_effect=unique_actor_count)

        async def count_actors(self, **kwargs):
            return await actor_count(self, **kwargs)

        monkeypatch.setattr(AnalyticsRepository, "unique_signal_actor_count", count_actors)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { uniqueLikers totalLikes uniqueSharers } }''',
            context_value=AppContext(
                db=AsyncMock() if legacy else object(),
                current_user=SimpleNamespace(id=creator_id),
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "uniqueLikers": unique_likers, "totalLikes": 0, "uniqueSharers": 0,
        }}
        like_calls = [
            call for call in actor_count.await_args_list
            if call.kwargs["signal_type"] == SignalType.LIKE
        ]
        assert len(like_calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("unique_savers", [4, 0])
    async def test_graphql_exposes_unique_savers(self, monkeypatch, legacy, unique_savers):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            AnalyticsRepository, "signal_totals",
            AsyncMock(return_value={
                SignalType.SAVE: {"count": 9, "total": 9.0},
                SignalType.UNSAVE: {"count": 9, "total": 9.0},
            } if unique_savers else {}),
        )

        async def unique_actor_count(**kwargs):
            assert kwargs["creator_id"] == creator_id
            assert kwargs["start"] == start
            assert kwargs["end"] == end
            return unique_savers if kwargs["signal_type"] == SignalType.SAVE else 0

        actor_count = AsyncMock(side_effect=unique_actor_count)
        monkeypatch.setattr(AnalyticsRepository, "unique_signal_actor_count", actor_count)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { uniqueSavers totalSaves } }''',
            context_value=AppContext(
                db=AsyncMock() if legacy else object(),
                current_user=SimpleNamespace(id=creator_id),
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "uniqueSavers": unique_savers, "totalSaves": 0,
        }}
        save_calls = [
            call for call in actor_count.await_args_list
            if call.kwargs["signal_type"] == SignalType.SAVE
        ]
        assert len(save_calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("has_data", [True, False])
    async def test_graphql_exposes_remaining_signal_actor_metrics(
        self, monkeypatch, legacy, has_data
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            AnalyticsRepository,
            "signal_totals",
            AsyncMock(return_value={
                SignalType.VIEW: {"count": 6, "total": 6.0},
                SignalType.REWATCH: {"count": 4, "total": 4.0},
                SignalType.COMPLETION: {"count": 6, "total": 6.0},
                SignalType.UNLIKE: {"count": 5, "total": 5.0},
                SignalType.UNSAVE: {"count": 3, "total": 3.0},
                SignalType.FOLLOW: {"count": 7, "total": 7.0},
                SignalType.NOT_INTERESTED: {"count": 8, "total": 8.0},
            } if has_data else {}),
        )

        actor_values = {
            SignalType.UNLIKE: 3,
            SignalType.UNSAVE: 2,
            SignalType.REWATCH: 4,
            SignalType.COMPLETION: 5,
            SignalType.FOLLOW: 6,
            SignalType.NOT_INTERESTED: 4,
        }

        async def check_actor_counts(**kwargs):
            assert kwargs["creator_id"] == creator_id
            assert kwargs["start"] == start
            assert kwargs["end"] == end
            return actor_values if has_data else {}

        actor_counts = AsyncMock(side_effect=check_actor_counts)

        async def unique_actor_counts(self, **kwargs):
            return await actor_counts(**kwargs)

        monkeypatch.setattr(
            AnalyticsRepository, "unique_signal_actor_counts", unique_actor_counts
        )
        async def unique_viewers(self, *, creator_id, post_ids=None, start=None, end=None):
            return {"total": 10 if has_data else 0, "by_post": {}}

        monkeypatch.setattr(AnalyticsRepository, "unique_viewer_counts", unique_viewers)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) {
                totalUnlikes uniqueUnlikers totalUnsaves uniqueUnsavers
                uniqueRewatchers uniqueRewatchRate totalCompletions uniqueCompleters uniqueCompletionRate
                uniqueNewFollowers uniqueNotInterestedUsers totalNotInterested
            } }''',
            context_value=AppContext(
                db=AsyncMock() if legacy else object(),
                current_user=SimpleNamespace(id=creator_id),
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "totalUnlikes": 5 if has_data else 0,
            "uniqueUnlikers": 3 if has_data else 0,
            "totalUnsaves": 3 if has_data else 0,
            "uniqueUnsavers": 2 if has_data else 0,
            "uniqueRewatchers": 4 if has_data else 0,
            "uniqueRewatchRate": 40.0 if has_data else None,
            "totalCompletions": 6 if has_data else 0,
            "uniqueCompleters": 5 if has_data else 0,
            "uniqueCompletionRate": 50.0 if has_data else None,
            "uniqueNewFollowers": 6 if has_data else 0,
            "uniqueNotInterestedUsers": 4 if has_data else 0,
            "totalNotInterested": 8 if has_data else 0,
        }}
        actor_counts.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize(
        "completions, views, expected_rate",
        [(4, 10, 40.0), (0, 10, 0.0), (0, 0, None)],
    )
    async def test_graphql_exposes_total_completions(
        self, monkeypatch, legacy, completions, views, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )

        async def signal_totals(self, **kwargs):
            assert kwargs["creator_id"] == creator_id
            if kwargs["start"] != start:
                return {SignalType.COMPLETION: {"count": 100, "total": 100.0}}
            assert kwargs["end"] == end
            if not views:
                return {}
            return {
                SignalType.VIEW: {"count": views - 2, "total": 999.0},
                SignalType.REWATCH: {"count": 2, "total": 999.0},
                SignalType.COMPLETION: {"count": completions, "total": 999.0},
            }

        monkeypatch.setattr(AnalyticsRepository, "signal_totals", signal_totals)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { totalCompletions totalViews completionRate } }''',
            context_value=AppContext(
                db=AsyncMock() if legacy else object(),
                current_user=SimpleNamespace(id=creator_id),
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "totalCompletions": completions,
            "totalViews": views,
            "completionRate": expected_rate,
        }}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unique_sharers", [4, 0])
    async def test_graphql_exposes_unique_sharers_from_service(
        self, monkeypatch, unique_sharers
    ):
        creator_id = uuid.uuid4()
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_signal_actor_count",
            AsyncMock(return_value=unique_sharers),
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { uniqueSharers } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {"uniqueSharers": unique_sharers}}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unique_sharers", [4, 0])
    async def test_legacy_graphql_exposes_unique_sharers(
        self, monkeypatch, unique_sharers
    ):
        creator_id = uuid.uuid4()
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            AnalyticsRepository,
            "unique_signal_actor_count",
            AsyncMock(return_value=unique_sharers),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { uniqueSharers } }''',
            context_value=AppContext(
                db=AsyncMock(), current_user=SimpleNamespace(id=creator_id)
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {"uniqueSharers": unique_sharers}}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unique_commenters", [5, 0])
    async def test_graphql_exposes_unique_commenters_from_service(
        self, monkeypatch, unique_commenters
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        unique_commenter_count = AsyncMock(return_value=unique_commenters)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_unique_commenters_for_creator",
            unique_commenter_count,
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { uniqueCommenters } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {"uniqueCommenters": unique_commenters}}
        unique_commenter_count.assert_awaited_once_with(creator_id, start, end)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unique_commenters", [5, 0])
    async def test_legacy_graphql_exposes_unique_commenters(
        self, monkeypatch, unique_commenters
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(
            "repositories.content_repository.PostRepository.get_by_user_id",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", AsyncMock(return_value={}))
        unique_commenter_count = AsyncMock(return_value=unique_commenters)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_unique_commenters_for_creator",
            unique_commenter_count,
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            "repositories.social_repository.FollowRepository.count_followers_since",
            AsyncMock(return_value=0),
        )

        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { uniqueCommenters } }''',
            context_value=AppContext(
                db=AsyncMock(), current_user=SimpleNamespace(id=creator_id)
            ),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {"uniqueCommenters": unique_commenters}}
        unique_commenter_count.assert_awaited_once_with(creator_id, start, end)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "views, rewatches, unique_viewers, expected_rate",
        [(8, 2, 3, 20.0), (0, 4, 1, 100.0), (5, 0, 2, 0.0), (0, 0, 0, None)],
    )
    async def test_creator_rewatch_rate_uses_event_counts(
        self, monkeypatch, views, rewatches, unique_viewers, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        service = CreatorAnalyticsService(AsyncMock())

        async def signal_totals(**kwargs):
            assert kwargs == {"creator_id": creator_id, "start": start, "end": end}
            return {
                SignalType.VIEW: {"count": views, "total": 999.0},
                SignalType.REWATCH: {"count": rewatches, "total": 999.0},
            }

        async def viewer_counts(**kwargs):
            return {"total": unique_viewers, "by_post": {}}

        monkeypatch.setattr(service.analytics_repo, "signal_totals", signal_totals)
        monkeypatch.setattr(service.analytics_repo, "unique_viewer_counts", viewer_counts)
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator", AsyncMock(return_value=0)
        )
        values = await service._period_totals(creator_id, start, end)

        assert values["views"] == views + rewatches
        assert values["rewatches"] == rewatches
        assert values["unique_viewers"] == unique_viewers
        assert values["rewatch_rate"] == expected_rate

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "views, rewatches, saves, unsaves, expected_rate",
        [(8, 2, 5, 2, 30.0), (8, 2, 1, 3, 0.0), (5, 0, 0, 0, 0.0),
         (0, 0, 0, 0, None), (0, 0, 3, 0, None)],
    )
    async def test_creator_save_rate_uses_net_events(
        self, monkeypatch, views, rewatches, saves, unsaves, expected_rate
    ):
        service = CreatorAnalyticsService(AsyncMock())
        signals = {
            SignalType.VIEW: {"count": views, "total": 999.0},
            SignalType.REWATCH: {"count": rewatches, "total": 999.0},
            SignalType.SAVE: {"count": saves, "total": 999.0},
            SignalType.UNSAVE: {"count": unsaves, "total": 999.0},
        }
        monkeypatch.setattr(service.analytics_repo, "signal_totals", AsyncMock(return_value=signals))
        monkeypatch.setattr(
            service.analytics_repo, "unique_viewer_counts",
            AsyncMock(return_value={"total": 1 if views + rewatches else 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator", AsyncMock(return_value=0)
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        values = await service._period_totals(uuid.uuid4(), start, start + timedelta(days=7))

        assert values["saves"] == max(0, saves - unsaves)
        assert values["save_rate"] == expected_rate

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "views, rewatches, shares, expected_rate",
        [(8, 2, 3, 30.0), (0, 4, 1, 25.0), (5, 0, 0, 0.0),
         (0, 0, 0, None), (0, 0, 3, None), (1, 0, 2, 200.0)],
    )
    async def test_creator_share_rate_uses_event_counts(
        self, monkeypatch, views, rewatches, shares, expected_rate
    ):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        service = CreatorAnalyticsService(AsyncMock())

        async def signal_totals(**kwargs):
            assert kwargs == {"creator_id": creator_id, "start": start, "end": end}
            return {
                SignalType.VIEW: {"count": views, "total": 999.0},
                SignalType.REWATCH: {"count": rewatches, "total": 999.0},
                SignalType.SHARE: {"count": shares, "total": 999.0},
            }

        monkeypatch.setattr(service.analytics_repo, "signal_totals", signal_totals)
        monkeypatch.setattr(
            service.analytics_repo, "unique_viewer_counts",
            AsyncMock(return_value={"total": 1 if views + rewatches else 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator", AsyncMock(return_value=0)
        )
        values = await service._period_totals(creator_id, start, end)

        assert values["shares"] == shares
        assert values["share_rate"] == expected_rate

    @pytest.mark.asyncio
    async def test_creator_like_and_comment_rates_use_net_interactions(self, monkeypatch):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        service = CreatorAnalyticsService(AsyncMock())
        monkeypatch.setattr(
            service.analytics_repo,
            "signal_totals",
            AsyncMock(return_value={
                SignalType.VIEW: {"count": 8, "total": 8.0},
                SignalType.REWATCH: {"count": 2, "total": 2.0},
                SignalType.LIKE: {"count": 5, "total": 5.0},
                SignalType.UNLIKE: {"count": 3, "total": 3.0},
            }),
        )
        monkeypatch.setattr(
            service.analytics_repo,
            "unique_viewer_counts",
            AsyncMock(return_value={"total": 4, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=2),
        )

        values = await service._period_totals(creator_id, start, end)

        assert values["likes"] == 2
        assert values["like_rate"] == 20.0
        assert values["comments"] == 2
        assert values["comment_rate"] == 20.0

    @pytest.mark.asyncio
    async def test_like_and_comment_rates_are_none_without_views(self, monkeypatch):
        service = CreatorAnalyticsService(AsyncMock())
        monkeypatch.setattr(service.analytics_repo, "signal_totals", AsyncMock(return_value={}))
        monkeypatch.setattr(
            service.analytics_repo,
            "unique_viewer_counts",
            AsyncMock(return_value={"total": 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator",
            AsyncMock(return_value=3),
        )

        values = await service._period_totals(
            uuid.uuid4(),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 8, tzinfo=timezone.utc),
        )

        assert values["like_rate"] is None
        assert values["comment_rate"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("has_data", [True, False])
    async def test_graphql_creator_engagement_rates_from_service(self, monkeypatch, has_data):
        creator_id = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        signals = {
            SignalType.VIEW: {"count": 8, "total": 8.0},
            SignalType.REWATCH: {"count": 2, "total": 2.0},
            SignalType.WATCH_DURATION: {"count": 10, "total": 150.0},
            SignalType.SAVE: {"count": 5, "total": 5.0},
            SignalType.UNSAVE: {"count": 2, "total": 2.0},
            SignalType.SHARE: {"count": 4, "total": 999.0},
            SignalType.NOT_INTERESTED: {"count": 3, "total": 3.0},
        } if has_data else {}

        async def signal_totals(self, **kwargs):
            assert kwargs["creator_id"] == creator_id
            if kwargs["start"] == start:
                assert kwargs["end"] == end
                return signals
            assert kwargs["end"] == start
            return {}

        monkeypatch.setattr(CreatorAnalyticsService, "_posts", AsyncMock(return_value=[]))
        monkeypatch.setattr(AnalyticsRepository, "signal_totals", signal_totals)
        monkeypatch.setattr(
            AnalyticsRepository, "unique_viewer_counts",
            AsyncMock(return_value={"total": 1 if has_data else 0, "by_post": {}}),
        )
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.count_for_creator", AsyncMock(return_value=0)
        )
        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
            AsyncMock(return_value=[]),
        )
        result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { totalViews uniqueViewers totalRewatches totalNotInterested rewatchRate totalLikes likeRate totalComments commentRate totalSaves saveRate totalShares shareRate } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )

        assert result.errors is None
        assert result.data == {"creatorAnalytics": {
            "totalViews": 10 if has_data else 0,
            "uniqueViewers": 1 if has_data else 0,
            "totalRewatches": 2 if has_data else 0,
            "totalNotInterested": 3 if has_data else 0,
            "rewatchRate": 20.0 if has_data else None,
            "totalLikes": 0,
            "likeRate": 0.0 if has_data else None,
            "totalComments": 0,
            "commentRate": 0.0 if has_data else None,
            "totalSaves": 3 if has_data else 0,
            "saveRate": 30.0 if has_data else None,
            "totalShares": 4 if has_data else 0,
            "shareRate": 40.0 if has_data else None,
        }}

        watch_result = await schema.execute(
            '''{ creatorAnalytics(period: {
                start: "2026-01-01T00:00:00+00:00", end: "2026-01-08T00:00:00+00:00"
            }) { totalWatchTime } }''',
            context_value=AppContext(db=object(), current_user=SimpleNamespace(id=creator_id)),
        )
        assert watch_result.errors is None
        assert watch_result.data == {
            "creatorAnalytics": {"totalWatchTime": 150.0 if has_data else 0.0}
        }

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
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.daily_counts_for_creator",
            AsyncMock(return_value=[]),
        )

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
        monkeypatch.setattr(
            "repositories.content_repository.CommentRepository.daily_counts_for_creator",
            AsyncMock(return_value=[]),
        )

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


class TestAnalyticsRepository:
    @pytest.mark.asyncio
    async def test_unique_viewer_counts_returns_creator_and_per_post_distinct_totals(self):
        post_id = uuid.uuid4()

        class Result:
            def all(self):
                return [
                    SimpleNamespace(post_id=None, unique_viewers=3),
                    SimpleNamespace(post_id=post_id, unique_viewers=2),
                ]

        db = AsyncMock()
        db.execute.return_value = Result()
        repository = AnalyticsRepository(db)

        result = await repository.unique_viewer_counts(
            creator_id=uuid.uuid4(), post_ids=[post_id]
        )

        statement = str(db.execute.await_args.args[0])
        assert "count(distinct(interaction_signals.user_id))" in statement
        assert result == {"total": 3, "by_post": {post_id: 2}}
