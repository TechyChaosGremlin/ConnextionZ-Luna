"""
Tests for post/comment social interaction resolvers in api/graphql.py.

Covers:
- Like -> Unlike (post)
- Save -> Unsave (post)
- Share (post)
- Comment creation/deletion
- Comment like/unlike
- Permission checks (auth required, author/admin-only actions, disabled comments)

Follows the pattern established in test_user_search.py: resolvers are called
directly with a lightweight AppContext, and repository methods are monkeypatched
so no real (Postgres-only) database is required.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from api.graphql import (
    AppContext,
    _add_comment,
    _delete_comment_legacy,
    _like_comment_legacy,
    _like_post_legacy,
    _mark_all_notifications_read,
    _mark_notification_read,
    _save_post_legacy,
    _share_post_legacy,
    _unread_notification_count,
)
from app.models.user import AccountStatus, User, UserRole
from repositories.social_repository import PostInteractionRepository


def make_user(role: UserRole = UserRole.USER, username: str = "alice") -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{username}@example.com",
        username=username,
        hashed_password="hashed",
        role=role,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )


def make_ctx(user: User | None) -> AppContext:
    return AppContext(db=AsyncMock(), current_user=user)


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch):
    async def noop_record(self, **kwargs):
        return None

    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.record", noop_record)
    monkeypatch.setattr("services.analytics_event_service.AnalyticsEventService.track_event", noop_record)


class ContentionSession:
    """Minimal session double that accepts just one interaction row per key."""

    def __init__(self):
        self.keys: set[tuple[str, str, str]] = set()
        self.statements = []
        self.lock = asyncio.Lock()

    async def execute(self, statement):
        self.statements.append(statement)
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT ON CONSTRAINT" in compiled
        params = statement.compile().params
        key = (statement.table.name, str(params["post_id"]), str(params["user_id"]))
        async with self.lock:
            inserted = key not in self.keys
            if inserted:
                self.keys.add(key)
        return SimpleNamespace(rowcount=int(inserted))

    async def flush(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "table_name"),
    [("toggle_like", "post_likes"), ("toggle_save", "post_saves")],
)
async def test_concurrent_like_or_save_inserts_only_one_interaction(monkeypatch, method_name, table_name):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = ContentionSession()

    async def no_existing_interaction(self, post_id, user_id):
        await asyncio.sleep(0)
        return False

    monkeypatch.setattr(PostInteractionRepository, "has_liked", no_existing_interaction)
    monkeypatch.setattr(PostInteractionRepository, "has_saved", no_existing_interaction)

    first, second = await asyncio.gather(
        getattr(PostInteractionRepository(session), method_name)(post_id, user_id),
        getattr(PostInteractionRepository(session), method_name)(post_id, user_id),
    )

    assert sorted((first, second)) == [False, True]
    assert session.keys == {(table_name, str(post_id), str(user_id))}


# ── Like / Unlike ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_like_then_unlike_post(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), like_count=0)
    ctx = make_ctx(user)

    liked_state = {"liked": False}

    async def fake_get_by_id(self, post_id):
        return post

    async def fake_has_liked(self, post_id, user_id):
        return liked_state["liked"]

    async def fake_toggle_like(self, post_id, user_id):
        liked_state["liked"] = not liked_state["liked"]
        return liked_state["liked"]

    async def fake_count_likes(self, post_id):
        return 1 if liked_state["liked"] else 0

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_liked", fake_has_liked)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.toggle_like", fake_toggle_like)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_likes", fake_count_likes)
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.create_notification",
        AsyncMock(),
    )

    liked_result = await _like_post_legacy(ctx, post.id, like=True)
    assert liked_result.liked is True
    assert liked_result.likes == 1

    unliked_result = await _like_post_legacy(ctx, post.id, like=False)
    assert unliked_result.liked is False
    assert unliked_result.likes == 0


@pytest.mark.asyncio
async def test_liking_already_liked_post_is_idempotent(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=user.id, like_count=1)
    ctx = make_ctx(user)

    toggle_calls = 0

    async def fake_get_by_id(self, post_id):
        return post

    async def fake_has_liked(self, post_id, user_id):
        return True  # already liked

    async def fake_toggle_like(self, post_id, user_id):
        nonlocal toggle_calls
        toggle_calls += 1
        return True

    async def fake_count_likes(self, post_id):
        return 1

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_liked", fake_has_liked)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.toggle_like", fake_toggle_like)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_likes", fake_count_likes)

    result = await _like_post_legacy(ctx, post.id, like=True)

    assert result.liked is True
    assert result.likes == 1
    # Own post -> re-liking a post you already liked should not re-toggle.
    assert toggle_calls == 0


@pytest.mark.asyncio
async def test_conflicted_like_does_not_notify_twice(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), like_count=1)
    ctx = make_ctx(user)
    notification = AsyncMock()

    async def fake_get_by_id(self, post_id):
        return post

    async def fake_has_liked(self, post_id, user_id):
        return False

    async def fake_toggle_like(self, post_id, user_id):
        return False

    async def fake_count_likes(self, post_id):
        return 1

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_liked", fake_has_liked)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.toggle_like", fake_toggle_like)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_likes", fake_count_likes)
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.create_notification", notification
    )

    result = await _like_post_legacy(ctx, post.id, like=True)

    assert result.liked is True
    assert result.likes == 1
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_like_post_requires_auth():
    ctx = make_ctx(None)
    with pytest.raises(PermissionError):
        await _like_post_legacy(ctx, uuid.uuid4(), like=True)


@pytest.mark.asyncio
async def test_like_post_not_found(monkeypatch):
    ctx = make_ctx(make_user())

    async def fake_get_by_id(self, post_id):
        return None

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)

    with pytest.raises(ValueError):
        await _like_post_legacy(ctx, uuid.uuid4(), like=True)


# ── Notification state ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unread_notification_count_uses_current_user(monkeypatch):
    user = make_user()
    ctx = make_ctx(user)
    unread_count = AsyncMock(return_value=3)
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.get_unread_count",
        unread_count,
    )

    assert await _unread_notification_count(ctx) == 3
    unread_count.assert_awaited_once_with(user.id)


@pytest.mark.asyncio
async def test_mark_notification_read_enforces_ownership(monkeypatch):
    user = make_user()
    notification = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    ctx = make_ctx(user)
    mark_as_read = AsyncMock()

    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.get_by_id",
        AsyncMock(return_value=notification),
    )
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.mark_as_read",
        mark_as_read,
    )

    with pytest.raises(PermissionError):
        await _mark_notification_read(ctx, notification.id)
    mark_as_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_all_notifications_read_scopes_to_current_user(monkeypatch):
    user = make_user()
    ctx = make_ctx(user)
    mark_all_as_read = AsyncMock(return_value=2)
    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.mark_all_as_read",
        mark_all_as_read,
    )

    assert await _mark_all_notifications_read(ctx) is True
    mark_all_as_read.assert_awaited_once_with(user.id)


# ── Save / Unsave ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_then_unsave_post(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), save_count=0)
    ctx = make_ctx(user)

    saved_state = {"saved": False}

    async def fake_get_by_id(self, post_id):
        return post

    async def fake_has_saved(self, post_id, user_id):
        return saved_state["saved"]

    async def fake_toggle_save(self, post_id, user_id):
        saved_state["saved"] = not saved_state["saved"]
        return saved_state["saved"]

    async def fake_count_saves(self, post_id):
        return 1 if saved_state["saved"] else 0

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_saved", fake_has_saved)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.toggle_save", fake_toggle_save)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_saves", fake_count_saves)

    saved_result = await _save_post_legacy(ctx, post.id, save=True)
    assert saved_result.saved is True
    assert saved_result.saves == 1

    unsaved_result = await _save_post_legacy(ctx, post.id, save=False)
    assert unsaved_result.saved is False
    assert unsaved_result.saves == 0


@pytest.mark.asyncio
async def test_save_post_requires_auth():
    ctx = make_ctx(None)
    with pytest.raises(PermissionError):
        await _save_post_legacy(ctx, uuid.uuid4(), save=True)


@pytest.mark.asyncio
async def test_save_post_not_found(monkeypatch):
    ctx = make_ctx(make_user())

    async def fake_get_by_id(self, post_id):
        return None

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)

    with pytest.raises(ValueError):
        await _save_post_legacy(ctx, uuid.uuid4(), save=True)


# ── Share ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_share_post(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), share_count=0)
    ctx = make_ctx(user)

    shared_ids: set[uuid.UUID] = set()

    async def fake_get_by_id(self, post_id):
        return post

    async def fake_add_share(self, post_id, user_id):
        if user_id in shared_ids:
            return False
        shared_ids.add(user_id)
        return True

    async def fake_count_shares(self, post_id):
        return len(shared_ids)

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.add_share", fake_add_share)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.count_shares", fake_count_shares)

    first = await _share_post_legacy(ctx, post.id)
    assert first.shared is True
    assert first.shares == 1

    # Sharing again should be idempotent for the same user (no double count).
    second = await _share_post_legacy(ctx, post.id)
    assert second.shared is True
    assert second.shares == 1


@pytest.mark.asyncio
async def test_share_post_requires_auth():
    ctx = make_ctx(None)
    with pytest.raises(PermissionError):
        await _share_post_legacy(ctx, uuid.uuid4())


@pytest.mark.asyncio
async def test_share_post_not_found(monkeypatch):
    ctx = make_ctx(make_user())

    async def fake_get_by_id(self, post_id):
        return None

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)

    with pytest.raises(ValueError):
        await _share_post_legacy(ctx, uuid.uuid4())


# ── Comment creation/deletion ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_comment(monkeypatch):
    user = make_user()
    post_owner_id = uuid.uuid4()
    post = SimpleNamespace(
        id=uuid.uuid4(), user_id=post_owner_id, allow_comments=True, comment_count=0
    )
    ctx = make_ctx(user)

    async def fake_get_by_id(self, post_id):
        return post

    async def fake_create(self, comment):
        comment.id = uuid.uuid4()
        return comment

    async def fake_get_profile_by_user_id(self, user_id):
        return None

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.content_repository.CommentRepository.create", fake_create)
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id",
        fake_get_profile_by_user_id,
    )
    notification = AsyncMock()
    monkeypatch.setattr("api.graphql._notify", notification)

    result = await _add_comment(ctx, post.id, "Nice post!")

    assert result.text == "Nice post!"
    assert result.can_delete is True
    assert post.comment_count == 1
    notification.assert_awaited_once()
    assert notification.await_args.kwargs["user_id"] == post_owner_id


@pytest.mark.asyncio
async def test_create_comment_requires_auth():
    ctx = make_ctx(None)
    with pytest.raises(PermissionError):
        await _add_comment(ctx, uuid.uuid4(), "hi")


@pytest.mark.asyncio
async def test_create_comment_fails_when_comments_disabled(monkeypatch):
    ctx = make_ctx(make_user())
    post = SimpleNamespace(id=uuid.uuid4(), allow_comments=False, comment_count=0)

    async def fake_get_by_id(self, post_id):
        return post

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)

    with pytest.raises(PermissionError):
        await _add_comment(ctx, post.id, "hi")


@pytest.mark.asyncio
async def test_create_comment_post_not_found(monkeypatch):
    ctx = make_ctx(make_user())

    async def fake_get_by_id(self, post_id):
        return None

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_by_id)

    with pytest.raises(ValueError):
        await _add_comment(ctx, uuid.uuid4(), "hi")


@pytest.mark.asyncio
async def test_author_can_delete_own_comment(monkeypatch):
    author = make_user(username="author")
    post = SimpleNamespace(id=uuid.uuid4(), comment_count=1)
    comment = SimpleNamespace(id=uuid.uuid4(), user_id=author.id, post_id=post.id)
    ctx = make_ctx(author)

    async def fake_get_comment_by_id(self, comment_id):
        return comment

    async def fake_soft_delete(self, comment_id):
        return True

    async def fake_get_post_by_id(self, post_id):
        return post

    monkeypatch.setattr("repositories.content_repository.CommentRepository.get_by_id", fake_get_comment_by_id)
    monkeypatch.setattr("repositories.content_repository.CommentRepository.soft_delete", fake_soft_delete)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_post_by_id)

    result = await _delete_comment_legacy(ctx, comment.id)

    assert result is True
    assert post.comment_count == 0


@pytest.mark.asyncio
async def test_delete_comment_requires_auth():
    ctx = make_ctx(None)
    with pytest.raises(PermissionError):
        await _delete_comment_legacy(ctx, uuid.uuid4())


@pytest.mark.asyncio
async def test_non_author_non_admin_cannot_delete_comment(monkeypatch):
    author = make_user(username="author")
    other_user = make_user(username="other")
    comment = SimpleNamespace(id=uuid.uuid4(), user_id=author.id, post_id=uuid.uuid4())
    ctx = make_ctx(other_user)

    async def fake_get_comment_by_id(self, comment_id):
        return comment

    monkeypatch.setattr("repositories.content_repository.CommentRepository.get_by_id", fake_get_comment_by_id)

    with pytest.raises(PermissionError):
        await _delete_comment_legacy(ctx, comment.id)


@pytest.mark.asyncio
async def test_admin_can_delete_other_users_comment(monkeypatch):
    author = make_user(username="author")
    admin = make_user(role=UserRole.ADMIN, username="mod")
    post = SimpleNamespace(id=uuid.uuid4(), comment_count=1)
    comment = SimpleNamespace(id=uuid.uuid4(), user_id=author.id, post_id=post.id)
    ctx = make_ctx(admin)

    async def fake_get_comment_by_id(self, comment_id):
        return comment

    async def fake_soft_delete(self, comment_id):
        return True

    async def fake_get_post_by_id(self, post_id):
        return post

    monkeypatch.setattr("repositories.content_repository.CommentRepository.get_by_id", fake_get_comment_by_id)
    monkeypatch.setattr("repositories.content_repository.CommentRepository.soft_delete", fake_soft_delete)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", fake_get_post_by_id)

    result = await _delete_comment_legacy(ctx, comment.id)

    assert result is True
    assert post.comment_count == 0


@pytest.mark.asyncio
async def test_delete_missing_comment_returns_false(monkeypatch):
    ctx = make_ctx(make_user())

    async def fake_get_comment_by_id(self, comment_id):
        return None

    monkeypatch.setattr("repositories.content_repository.CommentRepository.get_by_id", fake_get_comment_by_id)

    result = await _delete_comment_legacy(ctx, uuid.uuid4())

    assert result is False


# ── Comment like/unlike ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_like_then_unlike_comment(monkeypatch):
    user = make_user()
    comment = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), like_count=0)
    ctx = make_ctx(user)

    liked_state = {"liked": False}

    async def fake_get_by_id(self, comment_id):
        return comment

    async def fake_has_liked(self, comment_id, user_id):
        return liked_state["liked"]

    async def fake_toggle_like(self, comment_id, user_id):
        liked_state["liked"] = not liked_state["liked"]
        return liked_state["liked"]

    async def fake_count_likes(self, comment_id):
        return 1 if liked_state["liked"] else 0

    monkeypatch.setattr("repositories.content_repository.CommentRepository.get_by_id", fake_get_by_id)
    monkeypatch.setattr("repositories.social_repository.CommentInteractionRepository.has_liked", fake_has_liked)
    monkeypatch.setattr("repositories.social_repository.CommentInteractionRepository.toggle_like", fake_toggle_like)
    monkeypatch.setattr("repositories.social_repository.CommentInteractionRepository.count_likes", fake_count_likes)

    liked_result = await _like_comment_legacy(ctx, comment.id, like=True)
    assert liked_result.liked is True
    assert liked_result.likes == 1

    unliked_result = await _like_comment_legacy(ctx, comment.id, like=False)
    assert unliked_result.liked is False
    assert unliked_result.likes == 0


@pytest.mark.asyncio
async def test_like_comment_requires_auth():
    ctx = make_ctx(None)
    with pytest.raises(PermissionError):
        await _like_comment_legacy(ctx, uuid.uuid4(), like=True)


@pytest.mark.asyncio
async def test_like_comment_not_found(monkeypatch):
    ctx = make_ctx(make_user())

    async def fake_get_by_id(self, comment_id):
        return None

    monkeypatch.setattr("repositories.content_repository.CommentRepository.get_by_id", fake_get_by_id)

    with pytest.raises(ValueError):
        await _like_comment_legacy(ctx, uuid.uuid4(), like=True)
