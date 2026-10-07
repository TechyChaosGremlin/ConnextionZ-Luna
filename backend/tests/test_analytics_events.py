"""Tests for the Event Tracking v1 analytics system.

Covers:
- AnalyticsEvent model / table shape (columns, indexes, FKs) via SQLAlchemy
  metadata inspection — no live Postgres connection required.
- AnalyticsEventService validation, nullable associations, metadata
  sanitization, duration recording, timestamps, and failure isolation
  (analytics write failures must not raise).
- Bulk impression tracking.
- Integration-style checks that existing social/video/search/notification
  resolvers call the tracking service with the right event type, and that
  the primary action still succeeds even when analytics recording fails.

Follows the pattern established in test_social_interactions.py /
test_watch_tracking.py: resolvers are called directly with a lightweight
AppContext, and repository methods are monkeypatched so no real
(Postgres-only) database is required.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, MetaData, Table, Uuid, create_engine, select

from api.graphql import (
    AppContext,
    _add_comment,
    _follow,
    _like_post_legacy,
    _mark_notification_read,
    _profile,
    _save_post_legacy,
    _share_post_legacy,
    _track_post_watch,
    _unfollow,
)
from app.models.analytics import AnalyticsEvent, EventType, SignalType
from app.models.social import Follow
from app.models.user import AccountStatus, User, UserRole
from repositories.analytics_repository import AnalyticsRepository
from repositories.stream_engagement_repository import StreamEngagementRepository
from app.errors import ForbiddenError
from services.analytics_event_service import AnalyticsEventService


def make_user(username: str = "alice") -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{username}@example.com",
        username=username,
        hashed_password="hashed",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )


class FakeSession:
    """Minimal AsyncSession double that supports the SAVEPOINT pattern
    (``begin_nested``) used by AnalyticsEventService, recording every row
    that reaches a successful flush."""

    def __init__(self, fail: bool = False):
        self.added: list[object] = []
        self.fail = fail
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)

    async def flush(self):
        if self.fail:
            raise RuntimeError("simulated DB failure")

    async def commit(self):
        self.committed = True

    def begin_nested(self):
        @asynccontextmanager
        async def _cm():
            yield self

        return _cm()


# ── Model / table shape ──────────────────────────────────────────────────


class TestAnalyticsEventModel:
    def test_table_name_and_columns(self):
        table = AnalyticsEvent.__table__
        assert table.name == "analytics_events"
        column_names = set(table.columns.keys())
        assert column_names == {
            "id", "user_id", "event_type", "post_id", "target_user_id",
            "session_id", "duration_ms", "metadata", "paid_delivery_id",
            "paid_campaign_id", "created_at", "updated_at",
        }

    def test_nullable_associations(self):
        table = AnalyticsEvent.__table__
        for col in (
            "user_id", "post_id", "target_user_id", "session_id", "duration_ms",
            "metadata", "paid_delivery_id", "paid_campaign_id",
        ):
            assert table.columns[col].nullable is True
        assert table.columns["event_type"].nullable is False
        assert table.columns["created_at"].nullable is False

    def test_indexes_cover_required_columns(self):
        table = AnalyticsEvent.__table__
        indexed_columns: set[str] = set()
        for col_name in ("user_id", "event_type", "post_id", "target_user_id", "session_id"):
            assert table.columns[col_name].index is True
            indexed_columns.add(col_name)
        # created_at is indexed via an explicit table-level Index (not index=True).
        assert any(
            list(ix.columns.keys()) == ["created_at"] for ix in table.indexes
        )

    def test_foreign_keys(self):
        table = AnalyticsEvent.__table__
        fk_targets = {fk.target_fullname for fk in table.foreign_keys}
        assert "users.id" in fk_targets
        assert "posts.id" in fk_targets

    def test_all_event_types_have_unique_string_values(self):
        values = [e.value for e in EventType]
        assert len(values) == len(set(values))


# ── Service: validation, nullability, metadata, failure isolation ───────


class TestAnalyticsEventService:
    @pytest.mark.asyncio
    async def test_valid_event_creation(self):
        db = FakeSession()
        user = make_user()
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.LIKE_CREATED, user=user,
        )
        assert event is not None
        assert db.added == [event]
        assert event.event_type == EventType.LIKE_CREATED
        assert event.user_id == user.id

    @pytest.mark.asyncio
    async def test_invalid_event_type_is_rejected(self):
        db = FakeSession()
        event = await AnalyticsEventService(db).track_event(
            event_type="not_a_real_event_type",  # type: ignore[arg-type]
        )
        assert event is None
        assert db.added == []

    @pytest.mark.asyncio
    async def test_invalid_duration_is_rejected(self):
        db = FakeSession()
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.VIDEO_WATCHED, duration_ms=-5,
        )
        assert event is None
        assert db.added == []

    @pytest.mark.asyncio
    async def test_anonymous_event_supported(self):
        db = FakeSession()
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.SEARCH_PERFORMED, user=None,
        )
        assert event is not None
        assert event.user_id is None

    @pytest.mark.asyncio
    async def test_user_associated_event(self):
        db = FakeSession()
        user = make_user()
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.PROFILE_VIEWED, user=user,
        )
        assert event.user_id == user.id

    @pytest.mark.asyncio
    async def test_post_associated_event(self):
        db = FakeSession()
        post = SimpleNamespace(id=uuid.uuid4())
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.VIDEO_VIEWED, post=post,
        )
        assert event.post_id == post.id

    @pytest.mark.asyncio
    async def test_target_user_event(self):
        db = FakeSession()
        target = make_user("bob")
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.FOLLOW_CREATED, target_user=target,
        )
        assert event.target_user_id == target.id

    @pytest.mark.asyncio
    async def test_duration_recording(self):
        db = FakeSession()
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.VIDEO_WATCHED, duration_ms=12345,
        )
        assert event.duration_ms == 12345

    @pytest.mark.asyncio
    async def test_metadata_recording_and_sanitization(self):
        db = FakeSession()
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.SEARCH_PERFORMED,
            metadata={"result_count": 5, "password": "should-be-stripped"},
        )
        assert event.event_metadata == {"result_count": 5}

    @pytest.mark.asyncio
    async def test_timestamps_are_server_managed_not_client_supplied(self):
        # created_at/updated_at come from server_default=func.now() — the
        # service never sets them directly.
        db = FakeSession()
        event = await AnalyticsEventService(db).track_event(event_type=EventType.LIKE_REMOVED)
        assert "created_at" not in event.__dict__
        assert "updated_at" not in event.__dict__

    @pytest.mark.asyncio
    async def test_failed_write_does_not_raise(self):
        db = FakeSession(fail=True)
        event = await AnalyticsEventService(db).track_event(event_type=EventType.COMMENT_CREATED)
        assert event is None  # swallowed, not raised

    @pytest.mark.asyncio
    async def test_bulk_impressions(self):
        db = FakeSession()
        posts = [SimpleNamespace(id=uuid.uuid4()) for _ in range(3)]
        await AnalyticsEventService(db).track_impressions_bulk(posts=posts, session_id="sess-1")
        assert len(db.added) == 3
        assert all(e.event_type == EventType.VIDEO_IMPRESSION for e in db.added)
        assert all(e.session_id == "sess-1" for e in db.added)

    @pytest.mark.asyncio
    async def test_bulk_impressions_empty_is_noop(self):
        db = FakeSession()
        await AnalyticsEventService(db).track_impressions_bulk(posts=[])
        assert db.added == []

    @pytest.mark.asyncio
    async def test_bulk_impressions_failure_does_not_raise(self):
        db = FakeSession(fail=True)
        posts = [SimpleNamespace(id=uuid.uuid4())]
        await AnalyticsEventService(db).track_impressions_bulk(posts=posts)  # should not raise


# ── Integration: existing resolvers generate the right events ───────────


def make_ctx(user: User | None) -> AppContext:
    return AppContext(db=AsyncMock(), current_user=user, session_id="sess-abc")


@pytest.fixture(autouse=True)
def _stub_signal_analytics(monkeypatch):
    async def noop_record(self, **kwargs):
        return None

    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.record", noop_record)


@pytest.fixture
def spy_track_event(monkeypatch):
    calls: list[dict] = []

    async def fake_track_event(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(AnalyticsEventService, "track_event", fake_track_event)
    return calls


@pytest.mark.asyncio
async def test_like_created_event_only_on_new_like(spy_track_event, monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), like_count=0)
    ctx = make_ctx(user)

    async def fake_get_by_id(self, id):
        return post

    async def fake_has_liked(self, id, user_id):
        return False

    async def fake_toggle_like(self, id, user_id):
        return True

    async def fake_count_likes(self, id):
        return 1

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_liked", fake_has_liked)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.toggle_like", fake_toggle_like)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_likes", fake_count_likes)
    monkeypatch.setattr("repositories.notification_repository.NotificationRepository.create_notification", AsyncMock())

    result = await _like_post_legacy(ctx, post.id, like=True)

    assert result.liked is True
    assert [c["event_type"] for c in spy_track_event] == [EventType.LIKE_CREATED]


@pytest.mark.asyncio
@pytest.mark.parametrize("created", [True, False])
async def test_follow_created_event(spy_track_event, monkeypatch, created):
    user = make_user("alice")
    target = make_user("bob")
    ctx = make_ctx(user)

    async def fake_get_by_username(self, username):
        return target

    async def fake_follow(self, follower_id, followee_id):
        return created

    async def fake_count(self, user_id):
        return 1

    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_username", fake_get_by_username)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.follow", fake_follow)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.count_followers", fake_count)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.count_following", fake_count)
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id",
        AsyncMock(return_value=SimpleNamespace(follower_count=0, following_count=0)),
    )
    monkeypatch.setattr("api.graphql._notify", AsyncMock())

    await _follow(ctx, target.username)

    assert [c["event_type"] for c in spy_track_event] == (
        [EventType.FOLLOW_CREATED] if created else []
    )
    if created:
        assert spy_track_event[0]["target_user"] is target


@pytest.mark.asyncio
async def test_follow_from_active_creator_stream_records_stream_attribution(
    spy_track_event, monkeypatch
):
    user = make_user("alice")
    target = make_user("bob")
    stream_id = uuid.uuid4()
    ctx = make_ctx(user)
    record = AsyncMock()

    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.get_by_username",
        AsyncMock(return_value=target),
    )
    monkeypatch.setattr(
        StreamEngagementRepository,
        "authorize_follow",
        AsyncMock(return_value=SimpleNamespace(id=stream_id, owner_id=target.id)),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.follow",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(AnalyticsRepository, "record", record)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.count_followers",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.count_following",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id",
        AsyncMock(return_value=SimpleNamespace(follower_count=0, following_count=0)),
    )
    monkeypatch.setattr("api.graphql._notify", AsyncMock())

    await _follow(ctx, target.username, stream_id)

    record.assert_awaited_once_with(
        user_id=user.id,
        creator_id=target.id,
        signal_type=SignalType.FOLLOW,
        stream_session_id=stream_id,
    )
    assert [event["event_type"] for event in spy_track_event] == [EventType.FOLLOW_CREATED]


@pytest.mark.asyncio
async def test_follow_rejects_inactive_or_unowned_stream_attribution(monkeypatch):
    user = make_user("alice")
    target = make_user("bob")
    stream_id = uuid.uuid4()
    ctx = make_ctx(user)
    follow = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.get_by_username",
        AsyncMock(return_value=target),
    )
    monkeypatch.setattr(
        StreamEngagementRepository,
        "authorize_follow",
        AsyncMock(side_effect=ForbiddenError("Stream is not owned by the followed creator")),
    )
    monkeypatch.setattr("repositories.social_repository.FollowRepository.follow", follow)

    with pytest.raises(ForbiddenError, match="not owned by the followed creator"):
        await _follow(ctx, target.username, stream_id)
    follow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [True, False])
async def test_unfollow_removed_event(spy_track_event, monkeypatch, removed):
    user = make_user("alice")
    target = make_user("bob")
    ctx = make_ctx(user)

    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.get_by_username", AsyncMock(return_value=target)
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.unfollow", AsyncMock(return_value=removed)
    )
    record = AsyncMock()
    monkeypatch.setattr(AnalyticsRepository, "record", record)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.count_followers", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.count_following", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id",
        AsyncMock(return_value=SimpleNamespace(follower_count=0, following_count=0)),
    )

    result = await _unfollow(ctx, target.username)

    assert result.following is False
    ctx.db.commit.assert_awaited_once()
    assert [c["event_type"] for c in spy_track_event] == (
        [EventType.FOLLOW_REMOVED] if removed else []
    )
    if removed:
        record.assert_awaited_once_with(
            user_id=user.id, creator_id=target.id, signal_type=SignalType.UNFOLLOW
        )
        assert spy_track_event[0]["target_user"] is target
    else:
        record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("initially_following", [True, False])
async def test_repeated_unfollow_uses_actual_deletion_and_preserves_other_edges(
    spy_track_event, monkeypatch, initially_following
):
    user, target, other_user, other_creator = [
        make_user(name) for name in ("alice", "bob", "carol", "dave")
    ]
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    follows = Table(
        "follows", metadata,
        Column("id", Uuid, primary_key=True),
        Column("follower_id", Uuid, nullable=False),
        Column("following_id", Uuid, nullable=False),
    )
    metadata.create_all(engine)
    edges = [(other_user.id, target.id), (user.id, other_creator.id)]
    if initially_following:
        edges.append((user.id, target.id))
    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.get_by_username", AsyncMock(return_value=target)
    )
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id",
        AsyncMock(return_value=SimpleNamespace(follower_count=0, following_count=0)),
    )
    record = AsyncMock()
    monkeypatch.setattr(AnalyticsRepository, "record", record)
    try:
        with engine.connect() as connection:
            connection.execute(follows.insert(), [
                {"id": uuid.uuid4(), "follower_id": follower, "following_id": creator}
                for follower, creator in edges
            ])
            ctx = make_ctx(user)

            async def execute(statement):
                return connection.execute(statement)

            ctx.db.execute.side_effect = execute
            first = await _unfollow(ctx, target.username)
            second = await _unfollow(ctx, target.username)

            assert first.following is second.following is False
            assert first.followers == second.followers == 1
            assert first.following_count == second.following_count == 1
            assert [c["event_type"] for c in spy_track_event] == (
                [EventType.FOLLOW_REMOVED] if initially_following else []
            )
            assert record.await_count == int(initially_following)
            assert set(connection.execute(
                select(Follow.follower_id, Follow.following_id)
            ).all()) == {(other_user.id, target.id), (user.id, other_creator.id)}
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_save_created_event(spy_track_event, monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), save_count=0)
    ctx = make_ctx(user)

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_by_id", AsyncMock(return_value=post)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.has_saved", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.toggle_save", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.count_saves", AsyncMock(return_value=1)
    )

    result = await _save_post_legacy(ctx, post.id, save=True)

    assert result.saved is True
    assert [c["event_type"] for c in spy_track_event] == [EventType.SAVE_CREATED]


@pytest.mark.asyncio
async def test_share_created_event(spy_track_event, monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), share_count=0)
    ctx = make_ctx(user)

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_by_id", AsyncMock(return_value=post)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.add_share", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.count_shares", AsyncMock(return_value=1)
    )

    result = await _share_post_legacy(ctx, post.id)

    assert result.shared is True
    assert [c["event_type"] for c in spy_track_event] == [EventType.SHARE_CREATED]


@pytest.mark.asyncio
async def test_comment_created_event(spy_track_event, monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), comment_count=0, allow_comments=True)
    ctx = make_ctx(user)

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_by_id", AsyncMock(return_value=post)
    )
    monkeypatch.setattr(
        "repositories.content_repository.CommentRepository.create", AsyncMock()
    )
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("api.graphql._notify", AsyncMock())
    monkeypatch.setattr("api.graphql._notify_username_mentions", AsyncMock())

    await _add_comment(ctx, post.id, "hello world")

    assert [c["event_type"] for c in spy_track_event] == [EventType.COMMENT_CREATED]


@pytest.mark.asyncio
async def test_video_watch_events(spy_track_event, monkeypatch):
    user = make_user()
    ctx = make_ctx(user)
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), duration_sec=100.0, view_count=0)
    watch = SimpleNamespace(watched_seconds=95.0, completed=True, rewatched=False)

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_by_id", AsyncMock(return_value=post)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.track_watch", AsyncMock(return_value=watch)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.count_views", AsyncMock(return_value=1)
    )

    await _track_post_watch(ctx, post.id, watched_seconds=95.0, completed=True)

    event_types = [c["event_type"] for c in spy_track_event]
    assert event_types == [EventType.VIDEO_VIEWED, EventType.VIDEO_WATCHED, EventType.VIDEO_COMPLETED]


@pytest.mark.asyncio
async def test_video_skipped_event(spy_track_event, monkeypatch):
    user = make_user()
    ctx = make_ctx(user)
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), duration_sec=100.0, view_count=0)
    watch = SimpleNamespace(watched_seconds=5.0, completed=False, rewatched=False)

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_by_id", AsyncMock(return_value=post)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.track_watch", AsyncMock(return_value=watch)
    )
    monkeypatch.setattr(
        "repositories.social_repository.PostInteractionRepository.count_views", AsyncMock(return_value=1)
    )

    await _track_post_watch(ctx, post.id, watched_seconds=5.0, completed=False)

    event_types = [c["event_type"] for c in spy_track_event]
    assert event_types == [EventType.VIDEO_VIEWED, EventType.VIDEO_WATCHED, EventType.VIDEO_SKIPPED]


@pytest.mark.asyncio
async def test_profile_viewed_event_not_fired_for_own_profile(spy_track_event, monkeypatch):
    user = make_user()
    ctx = make_ctx(user)
    profile = SimpleNamespace(user_id=user.id, username=user.username)

    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_username", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr("api.graphql._profile_to_detail", AsyncMock(return_value=SimpleNamespace()))

    await _profile(ctx, None, user.username)

    assert spy_track_event == []


@pytest.mark.asyncio
async def test_profile_viewed_event_fired_for_other_profile(spy_track_event, monkeypatch):
    viewer = make_user("alice")
    owner = make_user("bob")
    ctx = make_ctx(viewer)
    profile = SimpleNamespace(user_id=owner.id, username=owner.username)

    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_username", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr("api.graphql._profile_to_detail", AsyncMock(return_value=SimpleNamespace()))

    await _profile(ctx, None, owner.username)

    assert [c["event_type"] for c in spy_track_event] == [EventType.PROFILE_VIEWED]
    assert spy_track_event[0]["target_user"].id == owner.id


@pytest.mark.asyncio
async def test_notification_opened_event(spy_track_event, monkeypatch):
    user = make_user()
    ctx = make_ctx(user)
    notification = SimpleNamespace(id=uuid.uuid4(), user_id=user.id, type=SimpleNamespace(value="new_like"))

    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.get_by_id",
        AsyncMock(return_value=notification),
    )
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.mark_as_read", AsyncMock()
    )

    result = await _mark_notification_read(ctx, notification.id)

    assert result is True
    assert [c["event_type"] for c in spy_track_event] == [EventType.NOTIFICATION_OPENED]


@pytest.mark.asyncio
async def test_primary_action_succeeds_even_if_analytics_recording_fails(monkeypatch):
    """The core failure-isolation guarantee, exercised end-to-end: a like
    still succeeds and commits even though the event write blows up."""
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), like_count=0)
    ctx = AppContext(db=AsyncMock(), current_user=user, session_id="sess-x")
    # Use a real (failing) AnalyticsEventService instead of the spy, wired to
    # a FakeSession whose flush() always raises.
    ctx.db = FakeSession(fail=True)
    ctx.db.commit = AsyncMock()  # AppContext code calls `await ctx.db.commit()`

    async def fake_get_by_id(self, id):
        return post

    async def fake_has_liked(self, id, user_id):
        return False

    async def fake_toggle_like(self, id, user_id):
        return True

    async def fake_count_likes(self, id):
        return 1

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_liked", fake_has_liked)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.toggle_like", fake_toggle_like)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_likes", fake_count_likes)
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.create_notification", AsyncMock()
    )
    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.record", AsyncMock())

    result = await _like_post_legacy(ctx, post.id, like=True)

    assert result.liked is True
    assert result.likes == 1
    ctx.db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_creator_affinity_nets_reversals_and_ignores_post_dismissals():
    viewer_id = uuid.uuid4()
    engaged_creator_id = uuid.uuid4()
    dismissed_creator_id = uuid.uuid4()
    result = SimpleNamespace(
        all=lambda: [
            (engaged_creator_id, SignalType.VIEW, 1.0),
            (engaged_creator_id, SignalType.WATCH_DURATION, 12.0),
            (engaged_creator_id, SignalType.LIKE, 1.0),
            (engaged_creator_id, SignalType.UNLIKE, 1.0),
            (engaged_creator_id, SignalType.SAVE, 1.0),
            (engaged_creator_id, SignalType.UNSAVE, 1.0),
            (engaged_creator_id, SignalType.SHARE, 1.0),
            (engaged_creator_id, SignalType.FOLLOW, 1.0),
            (engaged_creator_id, SignalType.UNFOLLOW, 1.0),
            (engaged_creator_id, SignalType.NOT_INTERESTED, 1.0),
            (dismissed_creator_id, SignalType.NOT_INTERESTED, 3.0),
        ]
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    affinity = await AnalyticsRepository(db).creator_affinity(viewer_id)

    assert affinity == [(engaged_creator_id, 14.0)]
    assert dismissed_creator_id not in dict(affinity)
    db.execute.assert_awaited_once()
