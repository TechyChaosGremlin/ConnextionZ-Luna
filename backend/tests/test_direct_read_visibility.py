from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from api.graphql import AppContext, _post, _profile, _user_posts
from app.models.content import ContentStatus
from tests.test_following_feed import make_post, make_profile, make_user


@pytest.fixture
def read_state(monkeypatch):
    creator = make_user("creator")
    viewer = make_user("viewer")
    profile = make_profile(creator)
    profile.bio = ""
    profile.location = ""
    profile.website_url = ""
    profile.online = False
    profile.collab_status = ""
    profile.response_time = ""
    post = make_post(creator.id, uuid.uuid4(), created_at=datetime.now(timezone.utc))
    post.deleted_at = None
    post.moderation_status = "approved"
    state = SimpleNamespace(
        creator=creator, viewer=viewer, profile=profile, posts=[post],
        following=False, blocked=False, muted=False,
    )

    async def get_post(self, post_id):
        return next((post for post in state.posts if post.id == post_id), None)

    async def get_posts(self, user_id, status=None, limit=20, before_id=None):
        posts = [post for post in state.posts if post.user_id == user_id]
        if before_id:
            posts = [post for post in posts if post.id < before_id]
        return posts[:limit]

    async def get_profile(self, user_id):
        return profile if user_id == creator.id else None

    async def get_profiles(self, user_ids):
        return [profile] if creator.id in user_ids else []

    async def get_profile_by_username(self, username):
        return profile if username == creator.username else None

    async def get_user(self, user_id):
        return creator if user_id == creator.id else viewer

    async def is_following(self, follower_id, following_id):
        return state.following and following_id == creator.id

    async def get_following_ids(self, user_id):
        return [creator.id] if state.following else []

    async def get_hidden_creator_ids(self, viewer_id, creator_ids):
        return {creator.id} if state.blocked or state.muted else set()

    async def get_playlists(self, profile_id):
        return []

    async def serialize_post(ctx, post):
        return SimpleNamespace(id=post.id)

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_user_id", get_posts)
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_user_id", get_profile)
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_multiple_by_user_ids", get_profiles)
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_username", get_profile_by_username)
    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", get_user)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.is_following", is_following)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.get_following_ids", get_following_ids)
    monkeypatch.setattr("repositories.social_repository.FeedSafetyRepository.get_hidden_creator_ids", get_hidden_creator_ids)
    monkeypatch.setattr("repositories.social_repository.PlaylistRepository.get_by_profile_id", get_playlists)
    monkeypatch.setattr("services.analytics_event_service.AnalyticsEventService.track_event", AsyncMock())
    monkeypatch.setattr("api.graphql._post_to_gql", lambda post: SimpleNamespace(id=post.id))
    monkeypatch.setattr("api.graphql._post_to_legacy_post", serialize_post)
    return state


async def read_post_ids(surface, state, viewer):
    ctx = AppContext(db=AsyncMock(), current_user=viewer)
    if surface == "post":
        result = await _post(ctx, str(state.posts[0].id))
        return [result.id] if result else []
    if surface == "user_posts":
        result = await _user_posts(ctx, str(state.creator.id), 12, None)
        return [edge.node.id for edge in result.edges]
    result = await _profile(ctx, None, state.creator.username)
    return [post.id for post in result.posts]


@pytest.mark.parametrize("surface", ["post", "user_posts", "profile"])
@pytest.mark.parametrize(
    "case,visible",
    [
        ("public", True),
        ("private_account", False),
        ("private_account_following", True),
        ("followers", False),
        ("followers_following", True),
        ("private_post", False),
        ("private_post_following", False),
        ("draft", False),
        ("scheduled", False),
        ("archived", False),
        ("flagged", False),
        ("removed_status", False),
        ("soft_deleted", False),
        ("pending", False),
        ("removed", False),
        ("blocked", False),
        ("blocked_by_creator", False),
        ("muted", False),
        ("blocked_following", False),
        ("muted_following", False),
        ("owner_private", True),
        ("owner_draft", True),
        ("owner_scheduled", True),
        ("owner_archived", False),
        ("owner_flagged", False),
        ("owner_removed_status", False),
        ("owner_soft_deleted", False),
        ("owner_removed", False),
    ],
)
@pytest.mark.asyncio
async def test_direct_reads_apply_visibility(read_state, surface, case, visible):
    state = read_state
    post = state.posts[0]
    viewer = state.creator if case.startswith("owner_") else state.viewer
    state.following = case.endswith("_following")
    if case.startswith("private_account") or case == "owner_private":
        state.profile.private_account = True
    if case.startswith("followers"):
        post.visibility = "followers"
    if case.startswith("private_post") or case == "owner_private":
        post.visibility = "private"
    if case in ("draft", "owner_draft"):
        post.status = ContentStatus.DRAFT
    if case in ("scheduled", "owner_scheduled"):
        post.status = ContentStatus.SCHEDULED
    if case in ("archived", "owner_archived"):
        post.status = ContentStatus.ARCHIVED
    if case in ("flagged", "owner_flagged"):
        post.status = ContentStatus.FLAGGED
    if case in ("removed_status", "owner_removed_status"):
        post.status = ContentStatus.REMOVED
    if case in ("soft_deleted", "owner_soft_deleted"):
        post.deleted_at = datetime.now(timezone.utc)
    if case in ("pending", "removed", "owner_removed"):
        post.moderation_status = "removed" if case != "pending" else "pending"
    state.blocked = case.startswith("blocked")
    state.muted = case.startswith("muted")

    assert await read_post_ids(surface, state, viewer) == ([post.id] if visible else [])


@pytest.mark.parametrize("case", ["public", "private_account", "followers", "private_post", "draft", "removed"])
@pytest.mark.asyncio
async def test_anonymous_profile_posts_respect_visibility(read_state, case):
    state = read_state
    post = state.posts[0]
    state.profile.private_account = case == "private_account"
    if case in ("followers", "private_post"):
        post.visibility = "followers" if case == "followers" else "private"
    if case == "draft":
        post.status = ContentStatus.DRAFT
    if case == "removed":
        post.moderation_status = "removed"
    assert await read_post_ids("profile", state, None) == ([post.id] if case == "public" else [])


@pytest.mark.asyncio
async def test_user_posts_and_profile_filter_mixed_results(read_state):
    state = read_state
    visible_post = state.posts[0]
    for field, value in [
        ("visibility", "private"),
        ("status", ContentStatus.DRAFT),
        ("moderation_status", "removed"),
        ("deleted_at", datetime.now(timezone.utc)),
    ]:
        hidden_post = SimpleNamespace(**vars(visible_post))
        hidden_post.id = uuid.uuid4()
        setattr(hidden_post, field, value)
        state.posts.append(hidden_post)

    ctx = AppContext(db=AsyncMock(), current_user=state.viewer)
    result = await _user_posts(ctx, str(state.creator.id), 12, None)
    assert [edge.node.id for edge in result.edges] == [visible_post.id]
    assert result.total_count == 1
    assert not result.page_info.has_next_page
    assert result.page_info.start_cursor == str(visible_post.id)
    assert result.page_info.end_cursor == str(visible_post.id)
    assert await read_post_ids("profile", state, state.viewer) == [visible_post.id]


@pytest.mark.parametrize("surface", ["post", "user_posts"])
@pytest.mark.asyncio
async def test_direct_post_reads_still_require_authentication(read_state, surface):
    with pytest.raises(ValueError, match="Authentication required"):
        await read_post_ids(surface, read_state, None)


@pytest.mark.parametrize("surface", ["post", "user_posts", "profile"])
@pytest.mark.asyncio
async def test_visibility_lookup_failure_does_not_expose_posts(read_state, monkeypatch, surface):
    monkeypatch.setattr(
        "repositories.social_repository.FeedSafetyRepository.get_hidden_creator_ids",
        AsyncMock(side_effect=RuntimeError("safety lookup failed")),
    )
    with pytest.raises(RuntimeError, match="safety lookup failed"):
        await read_post_ids(surface, read_state, read_state.viewer)