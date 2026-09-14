from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from api.graphql import AppContext, _search
from app.models.user import AccountStatus, User, UserRole
from repositories.content_repository import PostRepository
from repositories.user_repository import UserRepository


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch):
    async def noop_track_event(self, **kwargs):
        return None

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event", noop_track_event
    )


@pytest.mark.asyncio
async def test_user_search_accepts_string_type_filters(monkeypatch):
    user = User(
        id="11111111-1111-4111-8111-111111111111",
        email="alice@example.com",
        username="alice",
        hashed_password="hashed",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    ctx = AppContext(db=object(), current_user=user)
    assert ctx.user is user

    async def fake_following(self, follower_id):
        assert follower_id == user.id
        return []

    async def fake_user_search(self, query, viewer_id=None, following_ids=None, limit=20, offset=0):
        assert query == "alice"
        assert viewer_id == user.id
        assert following_ids == []
        assert limit == 21
        return [(user, 88.0)]

    async def fake_post_search(*args, **kwargs):
        raise AssertionError("posts should not be searched when type filter is USER")

    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.search_by_username_and_display_name",
        fake_user_search,
    )
    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.search_by_content",
        fake_post_search,
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        fake_following,
    )

    result = await _search(ctx, SimpleNamespace(query="alice", types=["USER"]), 20, None)

    assert len(result.edges) == 1
    assert result.total_count == 1
    assert result.edges[0].node.type == "USER"
    assert result.edges[0].node.user.username == "alice"


@pytest.mark.asyncio
async def test_user_search_accepts_enum_like_type_filters(monkeypatch):
    user = User(
        id="22222222-2222-4222-8222-222222222222",
        email="bob@example.com",
        username="bob",
        hashed_password="hashed",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    ctx = AppContext(db=object(), current_user=user)

    class SearchType:
        def __init__(self, value):
            self.value = value

    async def fake_following(self, follower_id):
        assert follower_id == user.id
        return []

    async def fake_user_search(self, query, viewer_id=None, following_ids=None, limit=20, offset=0):
        return [(user, 77.0)]

    async def fake_post_search(*args, **kwargs):
        raise AssertionError("posts should not be searched when type filter is USER")

    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.search_by_username_and_display_name",
        fake_user_search,
    )
    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.search_by_content",
        fake_post_search,
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        fake_following,
    )

    result = await _search(ctx, SimpleNamespace(query="bob", types=[SearchType("USER")]), 20, None)

    assert len(result.edges) == 1
    assert result.total_count == 1
    assert result.edges[0].node.type == "USER"


@pytest.mark.asyncio
async def test_search_posts_returns_legacy_feed_page_shape(monkeypatch):
    user = User(
        id="33333333-3333-4333-8333-333333333333",
        email="charlie@example.com",
        username="charlie",
        hashed_password="hashed",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    ctx = AppContext(db=object(), current_user=user)

    post = SimpleNamespace(
        id="44444444-4444-4444-8444-444444444444",
        user_id=user.id,
        caption="Launch day in Berlin",
        hashtags=["launch", "berlin"],
        audio="Original Sound",
        visibility="public",
        allow_comments=True,
        allow_collabs=True,
        duration_sec=12.5,
        like_count=10,
        comment_count=2,
        share_count=1,
        view_count=99,
        save_count=0,
        collab_with=None,
        thumbnail="",
        media_url="",
        status=SimpleNamespace(value="published"),
        scheduled_at=None,
    )
    profile = SimpleNamespace(
        id="55555555-5555-4555-8555-555555555555",
        user_id=user.id,
        display_name="Charlie Launch",
        avatar_url="",
        avatar_color="#00AEEF",
        verified=False,
        collab_score=0.0,
        collaboration_count=0,
        follower_count=12,
        following_count=7,
        open_to_collab=True,
        private_account=False,
    )

    async def fake_following(self, follower_id):
        assert follower_id == user.id
        return []

    async def fake_search(self, query, viewer_id=None, following_ids=None, limit=20, offset=0):
        assert query == "launch"
        assert viewer_id == user.id
        assert following_ids == []
        assert limit == 21
        return [(post, profile, 42.0)]

    monkeypatch.setattr(
        "repositories.content_repository.PostRepository.search_by_content",
        fake_search,
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        fake_following,
    )

    from api.graphql import Query

    result = await Query().search_posts(SimpleNamespace(context=ctx), "launch", None, 20, None, "relevance")

    assert result.items[0].caption == "Launch day in Berlin"
    assert result.items[0].creator.username == "charlie"
    assert result.items[0].creator.display_name == "Charlie Launch"


@pytest.mark.asyncio
async def test_unified_search_paginates_after_combining_results(monkeypatch):
    user = User(
        id="66666666-6666-4666-8666-666666666666",
        email="dana@example.com",
        username="dana",
        hashed_password="hashed",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    ctx = AppContext(db=object(), current_user=user)

    async def fake_following(self, follower_id):
        return []

    async def fake_user_search(self, query, viewer_id=None, following_ids=None, limit=20, offset=0):
        assert limit >= 3
        return [(user, 30.0), (user, 20.0), (user, 10.0)]

    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.search_by_username_and_display_name",
        fake_user_search,
    )
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        fake_following,
    )

    first = await _search(ctx, SimpleNamespace(query="dana", types=["USER"]), 2, None)
    second = await _search(ctx, SimpleNamespace(query="dana", types=["USER"]), 2, first.page_info.end_cursor)

    assert [edge.cursor for edge in first.edges] == ["1", "2"]
    assert first.page_info.has_next_page
    assert len(second.edges) == 1
    assert not second.page_info.has_next_page


@pytest.mark.asyncio
async def test_unified_search_returns_empty_for_blank_query_and_rejects_invalid_input():
    user = User(
        id="77777777-7777-4777-8777-777777777777",
        email="erin@example.com",
        username="erin",
        hashed_password="hashed",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    ctx = AppContext(db=object(), current_user=user)

    empty = await _search(ctx, SimpleNamespace(query="  ", types=["USER"]), 20, None)
    assert empty.edges == []
    assert empty.total_count == 0

    with pytest.raises(ValueError, match="first must be between 1 and 50"):
        await _search(ctx, SimpleNamespace(query="erin", types=["USER"]), 0, None)

    with pytest.raises(ValueError, match="Invalid cursor"):
        await _search(ctx, SimpleNamespace(query="erin", types=["USER"]), 20, "invalid")

    with pytest.raises(ValueError, match="Unsupported search type"):
        await _search(ctx, SimpleNamespace(query="erin", types=["SOUND"]), 20, None)


@pytest.mark.asyncio
async def test_user_search_query_enforces_account_visibility_and_block_rules():
    statements = []

    class RecordingSession:
        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(all=lambda: [])

    await UserRepository(RecordingSession()).search_by_username_and_display_name(
        "dana",
        viewer_id=uuid4(),
        following_ids=[uuid4()],
    )

    statement = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "users.deleted_at IS NULL" in statement
    assert "profiles.deleted_at IS NULL" in statement
    assert "users.status" in statement
    assert "profiles.private_account" in statement
    assert "user_blocks" in statement


@pytest.mark.asyncio
async def test_post_search_query_enforces_visibility_moderation_and_safety_rules():
    statements = []

    class RecordingSession:
        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(all=lambda: [])

    await PostRepository(RecordingSession()).search_by_content(
        "dana",
        viewer_id=uuid4(),
        following_ids=[uuid4()],
    )

    statement = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "posts.deleted_at IS NULL" in statement
    assert "posts.moderation_status" in statement
    assert "users.status" in statement
    assert "profiles.private_account" in statement
    assert "user_blocks" in statement
    assert "user_mutes" in statement
