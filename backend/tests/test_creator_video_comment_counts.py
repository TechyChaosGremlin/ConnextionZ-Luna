"""Period-scoped persisted comment counts for creator video analytics."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, DateTime, MetaData, Table, Uuid, create_engine

import api.graphql as graphql_module
from api.graphql import _creator_video_analytics
from repositories.content_repository import CommentRepository
from services.creator_analytics_service import CreatorAnalyticsService

START = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)
END = datetime(2026, 4, 4, 12, tzinfo=timezone.utc)


@pytest.fixture
def comment_db():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    posts = Table(
        "posts", metadata,
        Column("id", Uuid, primary_key=True),
        Column("user_id", Uuid, nullable=False),
    )
    comments = Table(
        "comments", metadata,
        Column("id", Uuid, primary_key=True),
        Column("post_id", Uuid, nullable=False),
        Column("user_id", Uuid, nullable=False),
        Column("parent_id", Uuid),
        Column("created_at", DateTime, nullable=False),
        Column("deleted_at", DateTime),
    )
    metadata.create_all(engine)
    creator_id, other_creator_id = uuid.uuid4(), uuid.uuid4()
    ids = SimpleNamespace(
        creator=creator_id,
        busy=uuid.uuid4(),
        quiet=uuid.uuid4(),
        silent=uuid.uuid4(),
        other=uuid.uuid4(),
    )
    top_level_id = uuid.uuid4()

    def comment(post_id, created_at, *, deleted_at=None, parent_id=None, comment_id=None):
        return {
            "id": comment_id or uuid.uuid4(), "post_id": post_id, "user_id": uuid.uuid4(),
            "parent_id": parent_id, "created_at": created_at, "deleted_at": deleted_at,
        }

    with engine.connect() as connection:
        connection.connection.driver_connection.create_function(
            "timezone", 2, lambda zone, timestamp: timestamp
        )
        connection.execute(posts.insert(), [
            {"id": ids.busy, "user_id": creator_id},
            {"id": ids.quiet, "user_id": creator_id},
            {"id": ids.silent, "user_id": creator_id},
            {"id": ids.other, "user_id": other_creator_id},
        ])
        connection.execute(comments.insert(), [
            # busy: 3 qualifying (start boundary, reply, end boundary)
            comment(ids.busy, START, comment_id=top_level_id),
            comment(ids.busy, START + timedelta(hours=1), parent_id=top_level_id),
            comment(ids.busy, END),
            comment(ids.busy, START - timedelta(microseconds=1)),
            comment(ids.busy, END + timedelta(microseconds=1)),
            comment(ids.busy, START + timedelta(hours=2), deleted_at=END),
            # quiet: 1 qualifying, plus older all-time history
            comment(ids.quiet, START + timedelta(days=1)),
            comment(ids.quiet, START - timedelta(days=30)),
            comment(ids.quiet, START - timedelta(days=31)),
            # another creator's post in period
            comment(ids.other, START + timedelta(hours=3)),
        ])

        async def execute(statement):
            return connection.execute(statement)

        yield SimpleNamespace(execute=AsyncMock(side_effect=execute)), ids
    engine.dispose()


def make_post(post_id, creator_id, comment_count, created_offset_days):
    return SimpleNamespace(
        id=post_id, user_id=creator_id, comment_count=comment_count,
        view_count=0, like_count=0, share_count=0, save_count=0,
        created_at=START + timedelta(days=created_offset_days),
    )


@pytest.fixture
def service_with_posts(comment_db, monkeypatch):
    db, ids = comment_db
    # Denormalized all-time counters deliberately disagree with the period counts.
    posts = [
        make_post(ids.busy, ids.creator, comment_count=1, created_offset_days=0),
        make_post(ids.quiet, ids.creator, comment_count=50, created_offset_days=1),
        make_post(ids.silent, ids.creator, comment_count=7, created_offset_days=2),
    ]
    service = CreatorAnalyticsService(db)
    monkeypatch.setattr(service, "_posts", AsyncMock(return_value=posts))
    monkeypatch.setattr(service.analytics_repo, "per_post_signal_totals", AsyncMock(return_value={}))
    monkeypatch.setattr(
        service.analytics_repo, "unique_viewer_counts",
        AsyncMock(return_value={"total": 0, "by_post": {}}),
    )
    return service, ids


@pytest.mark.asyncio
async def test_counts_by_post_scopes_to_period_post_and_non_deleted(comment_db):
    db, ids = comment_db

    counts = await CommentRepository(db).counts_by_post(
        [ids.busy, ids.quiet, ids.silent], START, END
    )

    assert counts == {ids.busy: 3, ids.quiet: 1}


@pytest.mark.asyncio
async def test_counts_by_post_without_post_ids_skips_query(comment_db):
    db, _ = comment_db

    assert await CommentRepository(db).counts_by_post([], START, END) == {}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_performance_uses_period_scoped_comment_counts(service_with_posts):
    service, ids = service_with_posts

    rows = await service.video_performance(ids.creator, START, END)

    by_post = {row["post"].id: row["comments"] for row in rows}
    assert by_post == {ids.busy: 3, ids.quiet: 1, ids.silent: 0}
    assert set(rows[0]) == {
        "post", "views", "unique_viewers", "likes", "comments", "shares", "saves",
        "watch_ms", "completed", "avg_watch_time", "completion_rate",
        "engagement_rate", "followers_generated",
    }


@pytest.mark.asyncio
async def test_video_performance_no_comments_in_period_returns_zero(service_with_posts):
    service, ids = service_with_posts
    empty_start = END + timedelta(days=10)

    rows = await service.video_performance(ids.creator, empty_start, empty_start + timedelta(days=1))

    assert [row["comments"] for row in rows] == [0, 0, 0]


@pytest.mark.asyncio
async def test_creator_video_analytics_comment_sort_uses_period_counts(
    service_with_posts, monkeypatch
):
    service, ids = service_with_posts
    original = CreatorAnalyticsService.video_performance
    monkeypatch.setattr(
        CreatorAnalyticsService,
        "video_performance",
        lambda self, creator_id, start, end: original(service, creator_id, start, end),
    )
    monkeypatch.setattr(graphql_module, "_post_to_gql", lambda post: post)
    ctx = SimpleNamespace(db=service.db, require_auth=lambda: SimpleNamespace(id=ids.creator))

    result = await _creator_video_analytics(
        ctx, SimpleNamespace(start=START, end=END), "comments"
    )

    assert [(item.post.id, item.comments) for item in result] == [
        (ids.busy, 3), (ids.quiet, 1), (ids.silent, 0),
    ]


@pytest.mark.asyncio
async def test_creator_period_and_daily_comment_totals_unchanged(comment_db):
    db, ids = comment_db
    repository = CommentRepository(db)

    total = await repository.count_for_creator(ids.creator, START, END)
    daily = await repository.daily_counts_for_creator(ids.creator, START, END)
    per_post = await repository.counts_by_post([ids.busy, ids.quiet, ids.silent], START, END)

    assert total == 4
    assert daily == [
        {"date": "2026-04-01", "comments": 2},
        {"date": "2026-04-02", "comments": 1},
        {"date": "2026-04-04", "comments": 1},
    ]
    assert sum(per_post.values()) == total
