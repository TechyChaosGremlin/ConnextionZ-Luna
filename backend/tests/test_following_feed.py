"""
Tests for the Following/For-You feed and follow/unfollow behavior.

Covers the checklist:
- Follow a creator -> their posts appear in the Following feed.
- Unfollow -> their posts disappear from the Following feed.
- Following feed and For You feed remain separate result sets.
- Feed pagination (cursor/limit) works.

Follows the pattern established in test_social_interactions.py: resolvers are
called directly with a lightweight AppContext, and repository methods are
monkeypatched so no real (Postgres-only) database is required. The follow
graph is backed by an in-memory dict shared across the monkeypatched
FollowRepository methods, so `_follow`/`_unfollow` really change what `_feed`
returns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, _feed, _follow, _unfollow
from app.models.content import ContentStatus
from app.models.user import AccountStatus, User, UserRole


def make_user(username: str) -> User:
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


def make_profile(user: User) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user.id,
        display_name=user.username,
        avatar_url="",
        avatar_color="#00AEEF",
        verified=False,
        collab_score=0.0,
        collaboration_count=0,
        follower_count=0,
        following_count=0,
        open_to_collab=True,
        private_account=False,
    )


def make_post(
    user_id: uuid.UUID,
    post_id: uuid.UUID,
    *,
    created_at=None,
    view_count: int = 0,
    like_count: int = 0,
    comment_count: int = 0,
    share_count: int = 0,
    save_count: int = 0,
    duration_sec: float = 0.0,
    tags: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=post_id,
        user_id=user_id,
        thumbnail="",
        media_url=None,
        caption="hello",
        view_count=view_count,
        like_count=like_count,
        collab_with=None,
        hashtags=[],
        tags=tags,
        audio="Original Sound",
        visibility="public",
        allow_comments=True,
        allow_collabs=True,
        duration_sec=duration_sec,
        comment_count=comment_count,
        share_count=share_count,
        save_count=save_count,
        created_at=created_at,
        status=ContentStatus.PUBLISHED,
        scheduled_at=None,
    )


def make_ctx(user: User) -> AppContext:
    return AppContext(db=AsyncMock(), current_user=user)


class FakeFollowGraph:
    """In-memory follower/following graph shared by monkeypatched FollowRepository methods.

    Methods here take the same arguments callers pass to FollowRepository (no
    leading `self`) so they can also be called directly from tests.
    """

    def __init__(self):
        self.edges: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def follow(self, follower_id, following_id):
        if (follower_id, following_id) in self.edges:
            return False
        self.edges.add((follower_id, following_id))
        return True

    async def unfollow(self, follower_id, following_id):
        self.edges.discard((follower_id, following_id))

    async def is_following(self, follower_id, following_id):
        return (follower_id, following_id) in self.edges

    async def get_following_ids(self, follower_id):
        return [b for (a, b) in self.edges if a == follower_id]

    async def count_followers(self, user_id):
        return sum(1 for (_, b) in self.edges if b == user_id)

    async def count_following(self, user_id):
        return sum(1 for (a, _) in self.edges if a == user_id)


@pytest.fixture
def follow_graph(monkeypatch):
    graph = FakeFollowGraph()
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.follow",
        lambda self, follower_id, following_id: graph.follow(follower_id, following_id),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.unfollow",
        lambda self, follower_id, following_id: graph.unfollow(follower_id, following_id),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.is_following",
        lambda self, follower_id, following_id: graph.is_following(follower_id, following_id),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        lambda self, follower_id: graph.get_following_ids(follower_id),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.count_followers",
        lambda self, user_id: graph.count_followers(user_id),
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.count_following",
        lambda self, user_id: graph.count_following(user_id),
    )
    return graph


@pytest.fixture(autouse=True)
def stub_shared_dependencies(monkeypatch):
    """Neutralize interaction/profile/notification lookups unrelated to feed filtering."""

    async def fake_get_hidden_creator_ids(self, viewer_id, creator_ids):
        return set()

    monkeypatch.setattr(
        "repositories.social_repository.FeedSafetyRepository.get_hidden_creator_ids",
        fake_get_hidden_creator_ids,
    )

    async def noop_track_impressions(self, **kwargs):
        return None

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_impressions_bulk",
        noop_track_impressions,
    )

    async def noop_track_event(self, **kwargs):
        return None

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event",
        noop_track_event,
    )

    async def fake_has_interaction(self, post_id, user_id):
        return False

    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_liked", fake_has_interaction)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_saved", fake_has_interaction)
    monkeypatch.setattr("repositories.social_repository.PostInteractionRepository.has_shared", fake_has_interaction)

    async def fake_get_profile_by_user_id(self, user_id):
        return make_profile(SimpleNamespace(id=user_id, username="creator"))

    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_by_user_id", fake_get_profile_by_user_id
    )

    async def fake_get_multiple_profiles(self, user_ids):
        return [make_profile(SimpleNamespace(id=uid, username="creator")) for uid in user_ids]

    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_multiple_by_user_ids",
        fake_get_multiple_profiles,
    )

    async def fake_user_get_by_id(self, entity_id):
        return make_user("creator")

    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", fake_user_get_by_id)

    async def fake_create_notification(self, **kwargs):
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(
        "repositories.notification_repository.NotificationRepository.create_notification",
        fake_create_notification,
    )

    async def fake_record_signal(self, **kwargs):
        return None

    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.record", fake_record_signal)

    async def fake_creator_affinity(self, user_id, limit=20):
        return []

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.creator_affinity", fake_creator_affinity
    )

    async def fake_viewer_post_history(self, user_id, post_ids):
        return {}

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.viewer_post_history",
        fake_viewer_post_history,
    )

    async def fake_get_discovery_pool(self, exclude_user_ids=None, since=None, limit=150):
        return []

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_discovery_pool", fake_get_discovery_pool
    )

    async def fake_post_engagement_rates(self, post_ids):
        return {}

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.post_engagement_rates",
        fake_post_engagement_rates,
    )

    async def fake_user_interest_tags(self, user_id, limit=20):
        return []

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.user_interest_tags",
        fake_user_interest_tags,
    )

    async def fake_get_interest_pool(
        self, interest_tags, exclude_user_ids=None, since=None, limit=60
    ):
        return []

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_interest_pool", fake_get_interest_pool
    )


def stub_feed_posts(monkeypatch, posts: list[SimpleNamespace]):
    """Patch PostRepository.get_feed to emulate DB filtering/ordering/pagination over `posts`."""

    async def fake_get_feed(self, user_ids, content_types=None, limit=20, before_id=None):
        candidates = [p for p in posts if p.user_id in user_ids]
        candidates.sort(key=lambda p: p.id, reverse=True)
        if before_id is not None:
            candidates = [p for p in candidates if p.id < before_id]
        return candidates[:limit]

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_feed", fake_get_feed)


def stub_hidden_creators(monkeypatch, creator_ids: set[uuid.UUID] | None = None):
    async def fake_get_hidden_creator_ids(self, viewer_id, candidate_ids):
        return creator_ids or set()

    monkeypatch.setattr(
        "repositories.social_repository.FeedSafetyRepository.get_hidden_creator_ids",
        fake_get_hidden_creator_ids,
    )


def stub_discovery_pool(monkeypatch, posts: list[SimpleNamespace]):
    """Patch PostRepository.get_discovery_pool to emulate the recent-public-posts pool."""

    async def fake_get_discovery_pool(self, exclude_user_ids=None, since=None, limit=150):
        excluded = set(exclude_user_ids or [])
        candidates = [p for p in posts if p.user_id not in excluded]
        return candidates[:limit]

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_discovery_pool", fake_get_discovery_pool
    )


def stub_creator_affinity(monkeypatch, affinity: dict[uuid.UUID, float]):
    async def fake_creator_affinity(self, user_id, limit=20):
        return list(affinity.items())

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.creator_affinity", fake_creator_affinity
    )


def stub_viewer_history(monkeypatch, history: dict):
    """Patch AnalyticsRepository.viewer_post_history to return per-post ViewerPostSignals."""

    async def fake_viewer_post_history(self, user_id, post_ids):
        return {pid: signals for pid, signals in history.items() if pid in set(post_ids)}

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.viewer_post_history",
        fake_viewer_post_history,
    )


def stub_user_interest_tags(monkeypatch, tags: list[str]):
    """Patch AnalyticsRepository.user_interest_tags to return demonstrated interest topics."""

    async def fake_user_interest_tags(self, user_id, limit=20):
        return list(tags)

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.user_interest_tags",
        fake_user_interest_tags,
    )


def stub_interest_pool(monkeypatch, posts: list[SimpleNamespace]):
    """Patch PostRepository.get_interest_pool to emulate tag-overlap filtering over `posts`."""

    async def fake_get_interest_pool(
        self, interest_tags, exclude_user_ids=None, since=None, limit=60
    ):
        if not interest_tags:
            return []
        excluded = set(exclude_user_ids or [])
        wanted = set(interest_tags)
        return [
            p
            for p in posts
            if p.user_id not in excluded and wanted & set(p.tags or [])
        ][:limit]

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_interest_pool", fake_get_interest_pool
    )


def stub_post_engagement_rates(monkeypatch, rates: dict):
    """Patch AnalyticsRepository.post_engagement_rates to return raw aggregate counts per post."""

    async def fake_post_engagement_rates(self, post_ids):
        return {pid: r for pid, r in rates.items() if pid in set(post_ids)}

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.post_engagement_rates",
        fake_post_engagement_rates,
    )


def _ordered_post_ids(n: int) -> list[uuid.UUID]:
    """Generate UUIDs that sort in creation order (like time-sortable UUIDv7 ids)."""
    return sorted(uuid.uuid4() for _ in range(n))


@pytest.mark.asyncio
async def test_follow_creator_then_their_posts_appear_in_following_feed(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    creator = make_user("creator")

    post_id = _ordered_post_ids(1)[0]
    stub_feed_posts(monkeypatch, [make_post(creator.id, post_id)])

    async def fake_get_by_username(self, username):
        return creator

    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_username", fake_get_by_username)

    ctx = make_ctx(viewer)

    # Before following: creator's posts do not show up in the Following feed.
    page_before = await _feed(ctx, cursor=None, limit=10, following=True)
    assert page_before.items == []

    # Follow the creator.
    await _follow(ctx, creator.username)
    assert await follow_graph.is_following(viewer.id, creator.id)

    # After following: creator's post appears in the Following feed.
    page_after = await _feed(ctx, cursor=None, limit=10, following=True)
    assert [item.id for item in page_after.items] == [post_id]


@pytest.mark.asyncio
async def test_unfollow_makes_creator_posts_disappear_from_following_feed(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    creator = make_user("creator")

    post_id = _ordered_post_ids(1)[0]
    stub_feed_posts(monkeypatch, [make_post(creator.id, post_id)])

    async def fake_get_by_username(self, username):
        return creator

    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_username", fake_get_by_username)

    ctx = make_ctx(viewer)

    await _follow(ctx, creator.username)
    page_following = await _feed(ctx, cursor=None, limit=10, following=True)
    assert [item.id for item in page_following.items] == [post_id]

    await _unfollow(ctx, creator.username)
    assert not await follow_graph.is_following(viewer.id, creator.id)

    page_after_unfollow = await _feed(ctx, cursor=None, limit=10, following=True)
    assert page_after_unfollow.items == []


@pytest.mark.asyncio
async def test_following_feed_and_for_you_feed_are_separate(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    followed = make_user("followed")
    stranger = make_user("stranger")

    followed_post_id, stranger_post_id, own_post_id = _ordered_post_ids(3)
    stub_feed_posts(
        monkeypatch,
        [
            make_post(followed.id, followed_post_id),
            make_post(stranger.id, stranger_post_id),
            make_post(viewer.id, own_post_id),
        ],
    )

    await follow_graph.follow(viewer.id, followed.id)

    ctx = make_ctx(viewer)

    following_page = await _feed(ctx, cursor=None, limit=10, following=True)
    for_you_page = await _feed(ctx, cursor=None, limit=10, following=False)

    following_ids = {item.id for item in following_page.items}
    for_you_ids = {item.id for item in for_you_page.items}

    # Following feed only contains posts from followed creators.
    assert following_ids == {followed_post_id}

    # For You feed includes the viewer's own posts and followed creators, but
    # never a stranger's posts, and is not identical to the Following feed
    # (it additionally includes the viewer's own content).
    assert for_you_ids == {followed_post_id, own_post_id}
    assert stranger_post_id not in for_you_ids
    assert stranger_post_id not in following_ids
    assert for_you_ids != following_ids


@pytest.mark.asyncio
async def test_feed_pagination_returns_next_cursor_and_subsequent_page(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    creator = make_user("creator")

    post_ids = _ordered_post_ids(3)  # oldest -> newest
    stub_feed_posts(monkeypatch, [make_post(creator.id, pid) for pid in post_ids])

    await follow_graph.follow(viewer.id, creator.id)
    ctx = make_ctx(viewer)

    first_page = await _feed(ctx, cursor=None, limit=2, following=True)
    # Newest-first ordering, limited to 2 of the 3 posts, with a cursor for more.
    assert [item.id for item in first_page.items] == [post_ids[2], post_ids[1]]
    assert first_page.next_cursor == str(post_ids[1])

    second_page = await _feed(ctx, cursor=first_page.next_cursor, limit=2, following=True)
    assert [item.id for item in second_page.items] == [post_ids[0]]
    assert second_page.next_cursor is None


@pytest.mark.asyncio
async def test_feed_excludes_hidden_deleted_and_unapproved_creator_posts(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    visible_creator = make_user("visible")
    blocked_creator = make_user("blocked")
    muted_creator = make_user("muted")
    deleted_creator = make_user("deleted")
    moderated_creator = make_user("moderated")
    post_ids = _ordered_post_ids(5)
    posts = [
        make_post(visible_creator.id, post_ids[0]),
        make_post(blocked_creator.id, post_ids[1]),
        make_post(muted_creator.id, post_ids[2]),
        make_post(deleted_creator.id, post_ids[3]),
        make_post(moderated_creator.id, post_ids[4]),
    ]
    posts[3].status = ContentStatus.REMOVED
    posts[4].moderation_status = "under_review"
    stub_feed_posts(monkeypatch, posts)
    stub_hidden_creators(monkeypatch, {blocked_creator.id, muted_creator.id})
    for creator in (visible_creator, blocked_creator, muted_creator, deleted_creator, moderated_creator):
        await follow_graph.follow(viewer.id, creator.id)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=True)

    assert [item.id for item in page.items] == [post_ids[0]]


@pytest.mark.asyncio
async def test_feed_enforces_private_and_post_visibility_rules(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    public_creator = make_user("public")
    private_creator = make_user("private")
    follower_only_creator = make_user("followers")
    post_ids = _ordered_post_ids(4)
    public_post = make_post(public_creator.id, post_ids[0])
    private_post = make_post(public_creator.id, post_ids[1])
    private_post.visibility = "private"
    account_private_post = make_post(private_creator.id, post_ids[2])
    follower_post = make_post(follower_only_creator.id, post_ids[3])
    follower_post.visibility = "followers"
    stub_feed_posts(monkeypatch, [public_post, private_post, account_private_post, follower_post])
    stub_hidden_creators(monkeypatch)

    async def fake_profile(self, user_id):
        return SimpleNamespace(
            id=uuid.uuid4(),
            user_id=user_id,
            display_name="creator",
            avatar_url="",
            avatar_color="#00AEEF",
            verified=False,
            collab_score=0.0,
            collaboration_count=0,
            follower_count=0,
            following_count=0,
            open_to_collab=True,
            private_account=user_id == private_creator.id,
        )

    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_user_id", fake_profile)

    async def fake_profiles(self, user_ids):
        return [await fake_profile(self, uid) for uid in user_ids]

    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_multiple_by_user_ids", fake_profiles
    )
    await follow_graph.follow(viewer.id, public_creator.id)
    await follow_graph.follow(viewer.id, follower_only_creator.id)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert {item.id for item in page.items} == {post_ids[0], post_ids[3]}


@pytest.mark.asyncio
async def test_feed_preserves_comment_and_collaboration_settings(monkeypatch, follow_graph):
    viewer = make_user("viewer")
    creator = make_user("creator")
    post_id = _ordered_post_ids(1)[0]
    post = make_post(creator.id, post_id)
    post.allow_comments = False
    post.allow_collabs = False
    stub_feed_posts(monkeypatch, [post])
    stub_hidden_creators(monkeypatch)
    await follow_graph.follow(viewer.id, creator.id)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=True)

    assert page.items[0].allow_comments is False
    assert page.items[0].allow_collabs is False


# ── For You: candidate generation, scoring, diversity, cold-start ──────────


@pytest.mark.asyncio
async def test_for_you_candidate_generation_includes_discovery_pool_beyond_follows(
    monkeypatch, follow_graph
):
    """A stranger's public post can surface in For You via the discovery
    pool even though the viewer doesn't follow them (unlike the Following
    feed, which never includes non-followed creators)."""
    viewer = make_user("viewer")
    stranger = make_user("stranger")
    stub_feed_posts(monkeypatch, [])  # nothing from viewer's own/followed pool
    stranger_post_id = _ordered_post_ids(1)[0]
    stub_discovery_pool(monkeypatch, [make_post(stranger.id, stranger_post_id)])
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [stranger_post_id]


@pytest.mark.asyncio
async def test_for_you_cold_start_uses_discovery_pool_with_no_follows_or_history(
    monkeypatch, follow_graph
):
    """A brand-new user with zero follows and zero interaction history still
    gets a populated For You feed from the discovery pool."""
    viewer = make_user("viewer")
    creator = make_user("creator")
    stub_feed_posts(monkeypatch, [])
    post_ids = _ordered_post_ids(3)
    stub_discovery_pool(monkeypatch, [make_post(creator.id, pid) for pid in post_ids])
    stub_hidden_creators(monkeypatch)
    stub_creator_affinity(monkeypatch, {})

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert {item.id for item in page.items} == set(post_ids)


@pytest.mark.asyncio
async def test_for_you_scoring_ranks_higher_engagement_above_stale_low_engagement(
    monkeypatch, follow_graph
):
    """Deterministic scoring should rank a highly-engaged post above a
    low-engagement one from a different creator, holding freshness equal."""
    viewer = make_user("viewer")
    popular_creator = make_user("popular")
    quiet_creator = make_user("quiet")
    now = datetime.now(timezone.utc)
    popular_id, quiet_id = _ordered_post_ids(2)
    popular_post = make_post(
        popular_creator.id, popular_id, created_at=now, view_count=1000,
        like_count=500, comment_count=100, share_count=50, save_count=50,
    )
    quiet_post = make_post(quiet_creator.id, quiet_id, created_at=now, view_count=10)
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [quiet_post, popular_post])
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items][0] == popular_id


@pytest.mark.asyncio
async def test_for_you_scoring_rewards_fresh_content_over_stale_content(monkeypatch, follow_graph):
    """Holding engagement equal, a fresher post should outrank a stale one."""
    viewer = make_user("viewer")
    creator_a = make_user("creator_a")
    creator_b = make_user("creator_b")
    now = datetime.now(timezone.utc)
    fresh_id, stale_id = _ordered_post_ids(2)
    fresh_post = make_post(creator_a.id, fresh_id, created_at=now)
    stale_post = make_post(creator_b.id, stale_id, created_at=now - timedelta(days=60))
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [stale_post, fresh_post])
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items][0] == fresh_id


@pytest.mark.asyncio
async def test_for_you_creator_affinity_boosts_ranking(monkeypatch, follow_graph):
    """A creator the viewer has strong watch/like history with (per
    AnalyticsRepository.creator_affinity) should outrank an otherwise
    identical, unaffiliated creator's post."""
    viewer = make_user("viewer")
    affine_creator = make_user("affine")
    neutral_creator = make_user("neutral")
    now = datetime.now(timezone.utc)
    affine_id, neutral_id = _ordered_post_ids(2)
    affine_post = make_post(affine_creator.id, affine_id, created_at=now)
    neutral_post = make_post(neutral_creator.id, neutral_id, created_at=now)
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [neutral_post, affine_post])
    stub_hidden_creators(monkeypatch)
    stub_creator_affinity(monkeypatch, {affine_creator.id: 25.0})

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items][0] == affine_id


@pytest.mark.asyncio
async def test_for_you_demotes_partially_watched_post(monkeypatch, follow_graph):
    """A post the viewer already partially watched should rank below an
    otherwise identical unseen post (demoted, not excluded)."""
    from repositories.feed_ranking import ViewerPostSignals

    viewer = make_user("viewer")
    creator_a = make_user("creator_a")
    creator_b = make_user("creator_b")
    now = datetime.now(timezone.utc)
    seen_id, unseen_id = _ordered_post_ids(2)
    seen_post = make_post(creator_a.id, seen_id, created_at=now)
    unseen_post = make_post(creator_b.id, unseen_id, created_at=now)
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [seen_post, unseen_post])
    stub_hidden_creators(monkeypatch)
    stub_viewer_history(monkeypatch, {seen_id: ViewerPostSignals(watched_seconds=12.0)})

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [unseen_id, seen_id]


@pytest.mark.asyncio
async def test_for_you_completed_post_demoted_below_partially_watched(monkeypatch, follow_graph):
    """Completion is a stronger 'already consumed' signal than a partial
    watch: unseen > partially watched > completed."""
    from repositories.feed_ranking import ViewerPostSignals

    viewer = make_user("viewer")
    creators = [make_user(f"creator_{c}") for c in "abc"]
    now = datetime.now(timezone.utc)
    completed_id, partial_id, unseen_id = _ordered_post_ids(3)
    posts = [
        make_post(creators[0].id, completed_id, created_at=now),
        make_post(creators[1].id, partial_id, created_at=now),
        make_post(creators[2].id, unseen_id, created_at=now),
    ]
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, posts)
    stub_hidden_creators(monkeypatch)
    stub_viewer_history(
        monkeypatch,
        {
            completed_id: ViewerPostSignals(watched_seconds=30.0, completed=True),
            partial_id: ViewerPostSignals(watched_seconds=30.0),
        },
    )

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [unseen_id, partial_id, completed_id]


@pytest.mark.asyncio
async def test_for_you_demotes_post_viewer_already_engaged_with(monkeypatch, follow_graph):
    """A post the viewer already liked/saved/shared should rank below an
    otherwise identical unengaged post."""
    from repositories.feed_ranking import ViewerPostSignals

    viewer = make_user("viewer")
    creator_a = make_user("creator_a")
    creator_b = make_user("creator_b")
    now = datetime.now(timezone.utc)
    engaged_id, fresh_id = _ordered_post_ids(2)
    engaged_post = make_post(creator_a.id, engaged_id, created_at=now)
    fresh_post = make_post(creator_b.id, fresh_id, created_at=now)
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [engaged_post, fresh_post])
    stub_hidden_creators(monkeypatch)
    stub_viewer_history(monkeypatch, {engaged_id: ViewerPostSignals(engaged=True)})

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [fresh_id, engaged_id]


@pytest.mark.asyncio
async def test_for_you_does_not_treat_own_posts_as_followed(monkeypatch, follow_graph):
    """Including a viewer's posts must not apply the follow-graph boost."""
    viewer = make_user("viewer")
    stranger = make_user("stranger")
    now = datetime.now(timezone.utc)
    own_id, stranger_id = _ordered_post_ids(2)
    own_post = make_post(viewer.id, own_id, created_at=now)
    stranger_post = make_post(stranger.id, stranger_id, created_at=now)
    stub_feed_posts(monkeypatch, [own_post])
    stub_discovery_pool(monkeypatch, [stranger_post])
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    # Equal posts use the stable UUID tie-breaker. The self-authored post
    # should not jump the queue through a follow boost that does not exist.
    assert [item.id for item in page.items][0] == stranger_id


@pytest.mark.asyncio
async def test_for_you_diversity_caps_consecutive_posts_per_creator(monkeypatch, follow_graph):
    """Even if one creator has the top-scoring posts, no more than
    MAX_CONSECUTIVE_PER_CREATOR of their posts should appear back-to-back."""
    from repositories.feed_ranking import MAX_CONSECUTIVE_PER_CREATOR

    viewer = make_user("viewer")
    dominant_creator = make_user("dominant")
    other_creator = make_user("other")
    now = datetime.now(timezone.utc)

    dominant_posts = [
        make_post(dominant_creator.id, pid, created_at=now, view_count=1000, like_count=500)
        for pid in _ordered_post_ids(4)
    ]
    other_post = make_post(other_creator.id, _ordered_post_ids(1)[0], created_at=now, view_count=1)
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, dominant_posts + [other_post])
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    creators_in_order = [
        dominant_creator.id if item.id != other_post.id else other_creator.id
        for item in page.items
    ]
    max_streak = 1
    streak = 1
    for prev, curr in zip(creators_in_order, creators_in_order[1:]):
        streak = streak + 1 if curr == prev else 1
        max_streak = max(max_streak, streak)
    assert max_streak <= MAX_CONSECUTIVE_PER_CREATOR


@pytest.mark.asyncio
async def test_for_you_excludes_blocked_and_muted_creators_from_discovery_pool(
    monkeypatch, follow_graph
):
    viewer = make_user("viewer")
    blocked_creator = make_user("blocked")
    muted_creator = make_user("muted")
    visible_creator = make_user("visible")
    post_ids = _ordered_post_ids(3)
    posts = [
        make_post(blocked_creator.id, post_ids[0]),
        make_post(muted_creator.id, post_ids[1]),
        make_post(visible_creator.id, post_ids[2]),
    ]
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, posts)
    stub_hidden_creators(monkeypatch, {blocked_creator.id, muted_creator.id})

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [post_ids[2]]


@pytest.mark.asyncio
async def test_for_you_pagination_is_stable_across_pages(monkeypatch, follow_graph):
    """Paginating with the cursor from page 1 should resume where page 1
    left off in ranked order, with no duplicates or gaps, given unchanged
    underlying data."""
    viewer = make_user("viewer")
    creator = make_user("creator")
    now = datetime.now(timezone.utc)
    post_ids = _ordered_post_ids(5)
    # Distinct view counts so ranking order is unambiguous/deterministic.
    posts = [
        make_post(creator.id, pid, created_at=now, view_count=(5 - i) * 100)
        for i, pid in enumerate(post_ids)
    ]
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, posts)
    stub_hidden_creators(monkeypatch)

    first_page = await _feed(make_ctx(viewer), cursor=None, limit=2, following=False)
    assert len(first_page.items) == 2
    assert first_page.next_cursor is not None

    second_page = await _feed(
        make_ctx(viewer), cursor=first_page.next_cursor, limit=2, following=False
    )
    assert len(second_page.items) == 2

    first_ids = [item.id for item in first_page.items]
    second_ids = [item.id for item in second_page.items]
    assert set(first_ids).isdisjoint(second_ids)
    # Ranked strictly by view_count desc here, so ids should appear in that order.
    assert first_ids + second_ids == post_ids[:4]


@pytest.mark.asyncio
async def test_for_you_pagination_snapshot_cursor_survives_mid_pagination_drift(
    monkeypatch, follow_graph
):
    """The snapshot cursor replays page 1's exact ranked order, so mid-pagination
    engagement changes neither duplicate nor reorder already-seen posts."""
    viewer = make_user("viewer")
    creator_a = make_user("creator_a")
    creator_b = make_user("creator_b")
    now = datetime.now(timezone.utc)
    a1_id, a2_id, b1_id, b2_id = _ordered_post_ids(4)
    # Page 1 ranking (by view count desc): a1, b1, a2, b2. No creator hits the
    # 2-consecutive streak cap, so diversify keeps pure score order.
    live_posts = {
        a1_id: make_post(creator_a.id, a1_id, created_at=now, view_count=400),
        b1_id: make_post(creator_b.id, b1_id, created_at=now, view_count=300),
        a2_id: make_post(creator_a.id, a2_id, created_at=now, view_count=200),
        b2_id: make_post(creator_b.id, b2_id, created_at=now, view_count=100),
    }
    stub_feed_posts(monkeypatch, [])
    stub_hidden_creators(monkeypatch)

    def restub_pool(posts_by_id):
        stub_discovery_pool(monkeypatch, list(posts_by_id.values()))

    restub_pool(live_posts)

    first_page = await _feed(make_ctx(viewer), cursor=None, limit=2, following=False)
    first_ids = [item.id for item in first_page.items]
    assert len(first_ids) == 2
    # Snapshot cursor embeds the full ranked order, not just the last id.
    assert first_page.next_cursor.startswith("fy1.")

    # Drift: b2's engagement jumps so it would now outrank the remaining posts,
    # and a brand-new top post enters the pool. Page 2 must continue the page-1
    # snapshot order — not re-rank b2 or inject the new post mid-feed.
    remaining_after_page1 = [pid for pid in (a1_id, b1_id, a2_id, b2_id) if pid not in first_ids]
    live_posts[b2_id].view_count = 999  # would now outrank a2 if re-ranked
    new_id = _ordered_post_ids(1)[0]
    live_posts[new_id] = make_post(creator_b.id, new_id, created_at=now, view_count=100000)
    restub_pool(live_posts)

    second_page = await _feed(
        make_ctx(viewer), cursor=first_page.next_cursor, limit=2, following=False
    )
    second_ids = [item.id for item in second_page.items]

    # Page 2 replays the snapshot's remaining posts in page-1 order, ignoring
    # the mid-pagination engagement change and the brand-new top post.
    assert second_ids == remaining_after_page1
    assert set(first_ids).isdisjoint(second_ids)


@pytest.mark.asyncio
async def test_for_you_pagination_accepts_legacy_plain_post_id_cursor(
    monkeypatch, follow_graph
):
    """Backward compatibility: a plain post-id cursor (pre-snapshot clients)
    still resumes by locating that post in the freshly computed ranking."""
    viewer = make_user("viewer")
    creator = make_user("creator")
    now = datetime.now(timezone.utc)
    post_ids = _ordered_post_ids(3)
    posts = [
        make_post(creator.id, pid, created_at=now, view_count=(3 - i) * 100)
        for i, pid in enumerate(post_ids)
    ]
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, posts)
    stub_hidden_creators(monkeypatch)

    page = await _feed(
        make_ctx(viewer), cursor=str(post_ids[0]), limit=2, following=False
    )

    assert [item.id for item in page.items] == post_ids[1:]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_for_you_pagination_rejects_malformed_cursor(monkeypatch, follow_graph):
    """A malformed cursor raises the same ValueError the Following feed raises."""
    viewer = make_user("viewer")
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [])
    stub_hidden_creators(monkeypatch)

    with pytest.raises(ValueError):
        await _feed(make_ctx(viewer), cursor="not-a-uuid", limit=2, following=False)


@pytest.mark.asyncio
async def test_for_you_affinity_pool_includes_unfollowed_affinity_creator(
    monkeypatch, follow_graph
):
    """A creator the viewer has demonstrated affinity for (per the unified
    signal log) but does NOT follow should still enter the candidate pool via
    the affinity pool."""
    viewer = make_user("viewer")
    affine_creator = make_user("affine")
    now = datetime.now(timezone.utc)
    affine_post_id = _ordered_post_ids(1)[0]
    # get_feed is shared by the personal and affinity pools; the fake returns
    # this post only when the affinity creator's id is queried.
    stub_feed_posts(monkeypatch, [make_post(affine_creator.id, affine_post_id, created_at=now)])
    stub_hidden_creators(monkeypatch)
    stub_creator_affinity(monkeypatch, {affine_creator.id: 42.0})

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert affine_post_id in {item.id for item in page.items}


@pytest.mark.asyncio
async def test_for_you_interest_pool_includes_tag_matching_posts(monkeypatch, follow_graph):
    """A stranger's post whose tags overlap the viewer's demonstrated interest
    topics enters the candidate pool via the interest pool."""
    viewer = make_user("viewer")
    stranger = make_user("stranger")
    other = make_user("other")
    now = datetime.now(timezone.utc)
    music_post_id, other_post_id = _ordered_post_ids(2)
    stub_feed_posts(monkeypatch, [])
    stub_interest_pool(
        monkeypatch,
        [
            make_post(stranger.id, music_post_id, created_at=now, tags=["music"]),
            make_post(other.id, other_post_id, created_at=now, tags=["sports"]),
        ],
    )
    stub_user_interest_tags(monkeypatch, ["music"])
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [music_post_id]


@pytest.mark.asyncio
async def test_for_you_dedupes_posts_appearing_in_multiple_pools(monkeypatch, follow_graph):
    """A post surfaced by several candidate pools must appear exactly once."""
    viewer = make_user("viewer")
    creator = make_user("creator")
    now = datetime.now(timezone.utc)
    post_id = _ordered_post_ids(1)[0]
    post = make_post(creator.id, post_id, created_at=now, tags=["music"])
    stub_feed_posts(monkeypatch, [])
    stub_interest_pool(monkeypatch, [post])
    stub_user_interest_tags(monkeypatch, ["music"])
    stub_discovery_pool(monkeypatch, [post])  # same post in both pools
    stub_hidden_creators(monkeypatch)

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [post_id]


@pytest.mark.asyncio
async def test_for_you_watch_quality_outranks_raw_popularity(monkeypatch, follow_graph):
    """Normalized watch quality/completion/rewatch (65% of the score) must let
    a genuinely well-watched small post outrank a raw-popularity post with no
    watch history — popularity alone must not dominate ranking."""
    viewer = make_user("viewer")
    niche_creator = make_user("niche")
    viral_creator = make_user("viral")
    now = datetime.now(timezone.utc)
    niche_id, viral_id = _ordered_post_ids(2)
    niche_post = make_post(
        niche_creator.id, niche_id, created_at=now, view_count=50, like_count=2,
        duration_sec=60.0,
    )
    viral_post = make_post(
        viral_creator.id, viral_id, created_at=now, view_count=100000,
        like_count=50000, share_count=5000, save_count=5000, duration_sec=60.0,
    )
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, [viral_post, niche_post])
    stub_hidden_creators(monkeypatch)
    # Real production signal aggregates: the niche post is watched ~90% through
    # with strong completion; the viral post has no watch signals at all.
    stub_post_engagement_rates(
        monkeypatch,
        {niche_id: {"views": 50.0, "watch_seconds": 2700.0, "completions": 40.0, "rewatches": 10.0}},
    )

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items][0] == niche_id


@pytest.mark.asyncio
async def test_for_you_rapid_skip_demotes_below_partial_watch(monkeypatch, follow_graph):
    """A rapid skip (very short watch relative to duration — a real production
    WATCH_DURATION signal with no completion) demotes that post more than a
    genuine partial watch; neither is hard-excluded."""
    from repositories.feed_ranking import ViewerPostSignals

    viewer = make_user("viewer")
    creators = [make_user(f"creator_{c}") for c in "abc"]
    now = datetime.now(timezone.utc)
    skipped_id, seen_id, unseen_id = _ordered_post_ids(3)
    posts = [
        make_post(creators[0].id, skipped_id, created_at=now, duration_sec=40.0),
        make_post(creators[1].id, seen_id, created_at=now, duration_sec=40.0),
        make_post(creators[2].id, unseen_id, created_at=now, duration_sec=40.0),
    ]
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, posts)
    stub_hidden_creators(monkeypatch)
    stub_viewer_history(
        monkeypatch,
        {
            skipped_id: ViewerPostSignals(watched_seconds=5.0),   # 12.5% < 25% threshold
            seen_id: ViewerPostSignals(watched_seconds=20.0),     # 50% — genuine partial watch
        },
    )

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [unseen_id, seen_id, skipped_id]


@pytest.mark.asyncio
async def test_not_interested_mutation_records_signal_and_is_idempotent(monkeypatch, follow_graph):
    """The not_interested mutation records a real NOT_INTERESTED signal once
    per (user, post) — repeat taps don't stack — and returns the flagged state."""
    from api.graphql import _not_interested

    viewer = make_user("viewer")
    creator = make_user("creator")
    post_id = _ordered_post_ids(1)[0]
    stub_feed_posts(monkeypatch, [make_post(creator.id, post_id)])

    async def fake_get_by_id(self, entity_id, include_deleted=False):
        return make_post(creator.id, post_id)

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.get_by_id", fake_get_by_id
    )

    recorded: list[dict] = []

    async def fake_record(self, **kwargs):
        recorded.append(kwargs)
        return None

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.record", fake_record
    )

    # viewer_post_history must reflect the NOT_INTERESTED signal after recording.
    flagged: set = set()

    async def fake_history(self, user_id, post_ids):
        from repositories.feed_ranking import ViewerPostSignals

        return {
            pid: ViewerPostSignals(not_interested=True)
            for pid in post_ids
            if pid in flagged
        }

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.viewer_post_history", fake_history
    )

    # Track the signal into our in-memory set when recorded.
    from app.models.analytics import SignalType

    async def record_and_flag(self, **kwargs):
        recorded.append(kwargs)
        if kwargs.get("signal_type") == SignalType.NOT_INTERESTED:
            flagged.add(kwargs["post_id"])
        return None

    monkeypatch.setattr(
        "repositories.analytics_repository.AnalyticsRepository.record", record_and_flag
    )

    ctx = make_ctx(viewer)
    first = await _not_interested(ctx, post_id)
    assert first.not_interested is True
    assert str(first.post_id) == str(post_id)
    ni_signals = [r for r in recorded if r.get("signal_type") == SignalType.NOT_INTERESTED]
    assert len(ni_signals) == 1
    assert ni_signals[0]["user_id"] == viewer.id
    assert ni_signals[0]["creator_id"] == creator.id

    # Repeat tap: no additional signal recorded (idempotent).
    second = await _not_interested(ctx, post_id)
    assert second.not_interested is True
    assert len([r for r in recorded if r.get("signal_type") == SignalType.NOT_INTERESTED]) == 1


@pytest.mark.asyncio
async def test_for_you_not_interested_post_demoted_below_consumed_posts(
    monkeypatch, follow_graph
):
    """A post the viewer marked 'Not Interested' ranks below even a completed
    post; it is demoted, never hard-excluded."""
    from repositories.feed_ranking import ViewerPostSignals

    viewer = make_user("viewer")
    creators = [make_user(f"creator_{c}") for c in "abc"]
    now = datetime.now(timezone.utc)
    flagged_id, completed_id, unseen_id = _ordered_post_ids(3)
    posts = [
        make_post(creators[0].id, flagged_id, created_at=now),
        make_post(creators[1].id, completed_id, created_at=now, duration_sec=30.0),
        make_post(creators[2].id, unseen_id, created_at=now),
    ]
    stub_feed_posts(monkeypatch, [])
    stub_discovery_pool(monkeypatch, posts)
    stub_hidden_creators(monkeypatch)
    stub_viewer_history(
        monkeypatch,
        {
            flagged_id: ViewerPostSignals(not_interested=True),
            completed_id: ViewerPostSignals(watched_seconds=30.0, completed=True),
        },
    )

    page = await _feed(make_ctx(viewer), cursor=None, limit=10, following=False)

    assert [item.id for item in page.items] == [unseen_id, completed_id, flagged_id]
