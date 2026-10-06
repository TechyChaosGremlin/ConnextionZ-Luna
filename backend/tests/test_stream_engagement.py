"""Native stream follow attribution, persistence, reporting and HTTP rate limits."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import Column, JSON, MetaData, Table, Uuid, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

import api.graphql as graphql
from api.graphql import AppContext, _follow, _unfollow, schema
from api.graphql_rate_limits import MUTATION_LIMITS
from app.errors import ConflictError, ForbiddenError, NotFoundError, register_exception_handlers
from app.models.analytics import AnalyticsEvent, InteractionSignal, SignalType
from app.models.social import Follow
from app.models.streaming import StreamSession, StreamSessionStatus, StreamViewerSession
from app.rate_limits import ActionRateLimiter, RATE_LIMIT_MESSAGE
from repositories.analytics_repository import AnalyticsRepository
from repositories.analytics_event_repository import AnalyticsEventRepository
from repositories.social_repository import FollowRepository
from repositories.stream_engagement_repository import StreamEngagementRepository
from repositories.stream_viewer_session_repository import StreamViewerSessionRepository
from services.creator_analytics_service import CreatorAnalyticsService
from services.analytics_event_service import AnalyticsEventService
from .test_creator_analytics import (
    persist_analytics_broadcast,
    streaming_analytics,
    streaming_overview,
)


@pytest.fixture(autouse=True)
def complete_graphql_analytics_sources(streaming_analytics, monkeypatch):
    monkeypatch.setattr(
        CreatorAnalyticsService,
        "_collaboration_totals",
        AsyncMock(
            return_value={
                "total_collaboration_requests": 0,
                "pending_collaborations": 0,
                "accepted_collaborations": 0,
                "declined_collaborations": 0,
                "cancelled_collaborations": 0,
                "collaboration_acceptance_rate": None,
                "collaboration_completion_rate": None,
                "average_response_hours": None,
                "active_collaborations": 0,
                "completed_collaborations": 0,
                "collaboration_success_rate": None,
            }
        ),
    )


@pytest_asyncio.fixture
async def follow_harness(streaming_analytics, monkeypatch):
    harness = streaming_analytics
    now = datetime.now(timezone.utc)
    harness.start = now - timedelta(seconds=5)
    harness.end = now + timedelta(seconds=5)
    stream = await persist_analytics_broadcast(
        harness, [(0, 0, 60, None)], status=StreamSessionStatus.ACTIVE, ended=None
    )
    harness.stream = stream
    harness.stream_id = stream.id
    harness.user = SimpleNamespace(id=harness.viewer_ids[0], username="viewer")
    harness.target = SimpleNamespace(id=harness.creator_id, username="creator")
    harness.ctx = AppContext(db=harness.db, current_user=harness.user)

    metadata = MetaData()
    Table("users", metadata, Column("id", Uuid(native_uuid=False), primary_key=True))
    Table("posts", metadata, Column("id", Uuid(native_uuid=False), primary_key=True))
    Follow.__table__.to_metadata(metadata)
    AnalyticsEvent.__table__.to_metadata(metadata)
    for column in metadata.tables["analytics_events"].columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    connection = await harness.db.connection()
    await connection.run_sync(metadata.create_all)
    await harness.db.commit()

    async def sqlite_follow(self, follower_id, following_id):
        # Exercise canonical uniqueness/RETURNING with the isolated test dialect.
        result = await self.db.execute(
            insert(Follow)
            .values(follower_id=follower_id, following_id=following_id)
            .on_conflict_do_nothing(index_elements=["follower_id", "following_id"])
            .returning(Follow.id)
        )
        await self.db.flush()
        return result.first() is not None

    monkeypatch.setattr(FollowRepository, "follow", sqlite_follow)
    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.get_by_username",
        AsyncMock(return_value=harness.target),
    )
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id",
        AsyncMock(return_value=None),
    )
    harness.track_event = AnalyticsEventService.track_event
    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event", AsyncMock()
    )
    monkeypatch.setattr(graphql, "_notify", AsyncMock())
    return harness


async def signals(harness):
    return (await harness.db.execute(select(InteractionSignal))).scalars().all()


@pytest.mark.asyncio
async def test_follow_persists_owner_and_stream_and_retry_does_not_change_event(follow_harness):
    h = follow_harness
    first = await _follow(h.ctx, "creator", h.stream.id)
    row = (await signals(h))[0]
    timestamp, signal_id = row.created_at, row.id
    second = await _follow(h.ctx, "creator", h.stream.id)
    assert first.following and second.following
    rows = await signals(h)
    assert len(rows) == 1
    assert rows[0].id == signal_id and rows[0].created_at == timestamp
    assert rows[0].creator_id == h.stream.owner_id
    assert rows[0].stream_session_id == h.stream.id
    assert rows[0].post_id is None and rows[0].signal_type == SignalType.FOLLOW
    assert h.stream.status == StreamSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_existing_follow_cannot_be_reattributed_and_real_refollow_is_new(follow_harness):
    h = follow_harness
    await _follow(h.ctx, "creator")
    await _follow(h.ctx, "creator", h.stream.id)
    assert (await signals(h))[0].stream_session_id is None
    await h.db.execute(delete(Follow).where(Follow.follower_id == h.user.id))
    await h.db.commit()
    await _follow(h.ctx, "creator", h.stream.id)
    assert len(await signals(h)) == 2
    result = await streaming_overview(h)
    assert result["stream_attributed_follows"] == 1
    assert result["unique_stream_followers"] == 1


@pytest.mark.asyncio
async def test_same_user_following_across_streams_counts_only_real_transitions(follow_harness):
    h = follow_harness
    first_id = h.stream.id
    await _follow(h.ctx, "creator", first_id)
    await h.db.execute(
        StreamSession.__table__.update()
        .where(StreamSession.id == first_id)
        .values(status=StreamSessionStatus.ENDED, ended_at=datetime.now(timezone.utc))
    )
    await h.db.commit()
    second = await persist_analytics_broadcast(
        h, [(0, 0, 60, None)], status=StreamSessionStatus.ACTIVE, ended=None
    )
    await _follow(h.ctx, "creator", second.id)
    assert len(await signals(h)) == 1
    await _unfollow(h.ctx, "creator")
    await _follow(h.ctx, "creator", second.id)
    rows = [row for row in await signals(h) if row.signal_type == SignalType.FOLLOW]
    assert {row.stream_session_id for row in rows} == {first_id, second.id}
    result = await streaming_overview(h)
    assert result["stream_attributed_follows"] == 2
    assert result["unique_stream_followers"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_product_event", [False, True])
async def test_ancillary_product_event_is_isolated_from_canonical_follow(
    follow_harness, monkeypatch, fail_product_event
):
    h = follow_harness
    monkeypatch.setattr(AnalyticsEventService, "track_event", h.track_event)
    warning = Mock()
    monkeypatch.setattr("services.analytics_event_service.logger.warning", warning)
    if fail_product_event:
        create_event = AnalyticsEventRepository.create_event

        async def invalid_event(self, event):
            event.user_id = uuid.uuid4()
            return await create_event(self, event)

        monkeypatch.setattr(AnalyticsEventRepository, "create_event", invalid_event)
    result = await _follow(h.ctx, "creator", h.stream.id)
    assert result.following
    assert not h.db.in_transaction()
    assert len(await signals(h)) == 1
    assert await h.db.scalar(select(func.count()).select_from(Follow)) == 1
    assert await h.db.scalar(select(func.count()).select_from(AnalyticsEvent)) == int(
        not fail_product_event
    )
    if fail_product_event:
        warning.assert_called_once_with(
            "analytics_event.record_failed", event_type="follow_created", exc_info=True
        )
    else:
        warning.assert_not_called()
    assert h.stream.status == StreamSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_follow_requires_authentication_before_any_lookup(follow_harness, monkeypatch):
    h = follow_harness
    authorize = AsyncMock()
    monkeypatch.setattr(StreamEngagementRepository, "authorize_follow", authorize)
    with pytest.raises(PermissionError):
        await _follow(AppContext(db=h.db, current_user=None), "creator", h.stream.id)
    authorize.assert_not_awaited()
    assert await signals(h) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "wrong_owner", "self"])
async def test_invalid_target_never_writes_a_follow(follow_harness, case):
    h = follow_harness
    stream_id = h.stream.id
    error = ForbiddenError
    if case == "missing":
        stream_id, error = uuid.uuid4(), NotFoundError
    elif case == "wrong_owner":
        h.stream.owner_id = h.other_creator_id
        await h.db.commit()
    else:
        h.target.id, error = h.user.id, ValueError
    with pytest.raises(error):
        await _follow(h.ctx, "creator", stream_id)
    assert not h.db.in_transaction()
    assert await signals(h) == []
    assert await h.db.scalar(select(func.count()).select_from(Follow)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["pending", "ended", "failed", "missing_start", "future_start", "finalized"]
)
async def test_invalid_stream_lifecycle_rejected(follow_harness, case):
    h = follow_harness
    if case in {"pending", "ended", "failed"}:
        h.stream.status = StreamSessionStatus(case)
    elif case == "missing_start":
        h.stream.started_at = None
    elif case == "future_start":
        h.stream.started_at = h.end + timedelta(minutes=1)
    else:
        h.stream.ended_at = h.start
    await h.db.commit()
    with pytest.raises(ConflictError, match="not active"):
        await _follow(h.ctx, "creator", h.stream.id)
    assert not h.db.in_transaction()
    assert await signals(h) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["no_lease", "other_user", "other_stream", "expired", "left", "future_join"]
)
async def test_follow_requires_own_active_lease_on_exact_stream(follow_harness, case):
    h = follow_harness
    viewer = (await h.db.execute(select(StreamViewerSession))).scalar_one()
    if case == "no_lease":
        await h.db.delete(viewer)
    elif case == "other_user":
        viewer.user_id = h.viewer_ids[1]
    elif case == "other_stream":
        other = await persist_analytics_broadcast(h, ended=1)
        viewer.stream_session_id = other.id
    elif case == "expired":
        viewer.lease_expires_at = h.start + timedelta(seconds=1)
    elif case == "left":
        viewer.left_at = h.start + timedelta(seconds=1)
    else:
        viewer.joined_at = h.end
    await h.db.commit()
    with pytest.raises(ForbiddenError, match="Join the active stream"):
        await _follow(h.ctx, "creator", h.stream.id)
    assert not h.db.in_transaction()
    assert await signals(h) == []


@pytest.mark.asyncio
async def test_authorization_lease_boundaries_are_half_open(follow_harness):
    h = follow_harness
    repository = StreamEngagementRepository(h.db)
    kwargs = dict(stream_id=h.stream.id, creator_id=h.creator_id, user_id=h.user.id)
    assert await repository.authorize_follow(**kwargs, at=h.start) is h.stream
    with pytest.raises(ForbiddenError):
        await repository.authorize_follow(**kwargs, at=h.start + timedelta(seconds=60))


@pytest.mark.asyncio
async def test_parent_lock_is_used_for_authorization(follow_harness, monkeypatch):
    h = follow_harness
    lock = AsyncMock(wraps=StreamViewerSessionRepository(h.db).lock_stream)
    monkeypatch.setattr(StreamViewerSessionRepository, "lock_stream", lock)
    await _follow(h.ctx, "creator", h.stream.id)
    lock.assert_awaited_once_with(h.stream.id)
    assert not h.db.in_transaction()


@pytest.mark.asyncio
async def test_signal_failure_rolls_back_follow_and_does_not_stop_stream(
    follow_harness, monkeypatch
):
    h = follow_harness
    monkeypatch.setattr(
        AnalyticsRepository, "record", AsyncMock(side_effect=RuntimeError("write failed"))
    )
    with pytest.raises(RuntimeError, match="write failed"):
        await _follow(h.ctx, "creator", h.stream.id)
    assert not h.db.in_transaction()
    assert await h.db.scalar(select(func.count()).select_from(Follow)) == 0
    assert await signals(h) == []
    assert (
        await h.db.scalar(select(StreamSession.status).where(StreamSession.id == h.stream_id))
        == StreamSessionStatus.ACTIVE
    )


@pytest.mark.asyncio
async def test_stream_follow_reporting_scopes_period_creator_and_distinct_users(
    streaming_analytics,
):
    h = streaming_analytics
    first = await persist_analytics_broadcast(h)
    second = await persist_analytics_broadcast(h, started=30, ended=90)
    unrelated = await persist_analytics_broadcast(h, owner_id=h.other_creator_id)
    rows = [
        (first.id, h.creator_id, 0, 0),
        (first.id, h.creator_id, 0, 1),
        (second.id, h.creator_id, 0, 30),
        (second.id, h.creator_id, 1, 40),
        (first.id, h.creator_id, 2, -1),
        (second.id, h.creator_id, 2, 60),
        (second.id, h.creator_id, 2, 61),
        (unrelated.id, h.other_creator_id, 2, 1),
        (unrelated.id, h.creator_id, 2, 2),
        (None, h.creator_id, 2, 2),
    ]
    h.db.add_all(
        [
            InteractionSignal(
                user_id=h.viewer_ids[actor],
                creator_id=creator,
                stream_session_id=stream,
                signal_type=SignalType.FOLLOW,
                value=1.0,
                created_at=h.start + timedelta(seconds=offset),
            )
            for stream, creator, actor, offset in rows
        ]
    )
    await h.db.commit()
    expected = {"stream_attributed_follows": 4, "unique_stream_followers": 2}
    assert (
        await h.service.analytics_repo.creator_stream_follow_totals(
            creator_id=h.creator_id, start=h.start, end=h.end
        )
        == expected
    )
    result = await streaming_overview(h)
    assert {key: result[key] for key in expected} == expected
    response = await schema.execute(
        f"""{{ creatorAnalytics(period: {{
            start: "{h.start.isoformat()}", end: "{h.end.isoformat()}"
        }}) {{ streamAttributedFollows uniqueStreamFollowers totalViews totalLikes }} }}""",
        context_value=AppContext(db=h.db, current_user=SimpleNamespace(id=h.creator_id)),
    )
    assert response.errors is None
    assert response.data == {
        "creatorAnalytics": {
            "streamAttributedFollows": 4,
            "uniqueStreamFollowers": 2,
            "totalViews": 4,
            "totalLikes": 2,
        }
    }


@pytest.mark.asyncio
async def test_zero_stream_engagement_and_graphql_auth(streaming_analytics):
    h = streaming_analytics
    result = await streaming_overview(h)
    assert result["stream_attributed_follows"] == result["unique_stream_followers"] == 0
    query = f"""{{ creatorAnalytics(period: {{
        start: "{h.start.isoformat()}", end: "{h.end.isoformat()}"
    }}) {{ streamAttributedFollows uniqueStreamFollowers }} }}"""
    response = await schema.execute(
        query, context_value=AppContext(db=h.db, current_user=SimpleNamespace(id=h.creator_id))
    )
    assert response.errors is None
    assert response.data["creatorAnalytics"] == {
        "streamAttributedFollows": 0,
        "uniqueStreamFollowers": 0,
    }
    denied = await schema.execute(query, context_value=AppContext(db=h.db, current_user=None))
    assert denied.errors and denied.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "code", "status"),
    [
        ("missing", "NOT_FOUND", 404),
        ("wrong_owner", "FORBIDDEN", 403),
        ("inactive", "CONFLICT", 409),
        ("no_lease", "FORBIDDEN", 403),
    ],
)
async def test_graphql_attribution_errors_have_standard_codes(follow_harness, case, code, status):
    h = follow_harness
    stream_id = h.stream.id
    if case == "missing":
        stream_id = uuid.uuid4()
    elif case == "wrong_owner":
        h.stream.owner_id = h.other_creator_id
    elif case == "inactive":
        h.stream.status = StreamSessionStatus.ENDED
    else:
        await h.db.execute(delete(StreamViewerSession))
    await h.db.commit()
    response = await schema.execute(
        f"""mutation {{
            follow(username: "creator", streamSessionId: "{stream_id}") {{ following }}
        }}""",
        context_value=h.ctx,
    )
    assert response.errors and response.data is None
    assert response.errors[0].extensions == {"code": code, "statusCode": status}
    assert not h.db.in_transaction()
    assert await signals(h) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_stream", "post_signal", "post_follow"])
async def test_database_rejects_invalid_stream_associations(follow_harness, case):
    h = follow_harness
    post_id = None
    if case == "post_follow":
        post_id = uuid.uuid4()
        await h.db.execute(
            Table("posts", MetaData(), Column("id", Uuid, primary_key=True))
            .insert()
            .values(id=post_id)
        )
    h.db.add(
        InteractionSignal(
            user_id=h.user.id,
            creator_id=h.creator_id,
            stream_session_id=uuid.uuid4() if case == "missing_stream" else h.stream.id,
            signal_type=SignalType.LIKE if case == "post_signal" else SignalType.FOLLOW,
            post_id=post_id,
            value=1.0,
        )
    )
    with pytest.raises(IntegrityError):
        await h.db.flush()
    await h.db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signal_type",
    [SignalType.LIKE, SignalType.UNLIKE, SignalType.SHARE, SignalType.SAVE, SignalType.UNSAVE],
)
async def test_repository_rejects_fake_stream_post_engagement(follow_harness, signal_type):
    h = follow_harness
    with pytest.raises(ValueError, match="Only creator follow"):
        await AnalyticsRepository(h.db).record(
            user_id=h.user.id,
            creator_id=h.creator_id,
            stream_session_id=h.stream.id,
            signal_type=signal_type,
        )
    assert await signals(h) == []


@pytest.mark.asyncio
async def test_attributed_follow_replays_use_existing_http_429(follow_harness, monkeypatch):
    h = follow_harness
    now = [100.0]
    router = graphql.create_graphql_router(lambda: h.db)
    router.action_limiter = ActionRateLimiter(MUTATION_LIMITS, clock=lambda: now[0])
    monkeypatch.setattr(
        graphql, "_graphql_user_from_token", AsyncMock(return_value=(h.user, "session"))
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/graphql")
    query = f'mutation {{ follow(username: "creator", streamSessionId: "{h.stream.id}") {{ following }} }}'
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(20):
            response = await client.post(
                "/graphql", json={"query": query}, headers={"Authorization": "Bearer test"}
            )
            assert response.status_code == 200
            assert response.json() == {"data": {"follow": {"following": True}}}
        response = await client.post(
            "/graphql", json={"query": query}, headers={"Authorization": "Bearer test"}
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        assert response.json() == {
            "error": {"code": "TOO_MANY_REQUESTS", "message": RATE_LIMIT_MESSAGE}
        }
    assert len(await signals(h)) == 1


@pytest.mark.asyncio
async def test_invalid_reporting_period_rejected(streaming_analytics):
    h = streaming_analytics
    with pytest.raises(ValueError, match="end must be after"):
        await h.service.analytics_repo.creator_stream_follow_totals(
            creator_id=h.creator_id, start=h.end, end=h.start
        )
