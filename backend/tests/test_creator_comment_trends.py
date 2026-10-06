"""Persisted comment aggregation and creator trend integration."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, DateTime, MetaData, Table, Uuid, create_engine
from sqlalchemy.dialects import postgresql

from repositories.content_repository import CommentRepository
from services.creator_analytics_service import CreatorAnalyticsService


@pytest.fixture
def comment_database():
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
        Column("created_at", DateTime, nullable=False),
        Column("deleted_at", DateTime),
    )
    metadata.create_all(engine)
    creator_id, other_creator_id = uuid.uuid4(), uuid.uuid4()
    post_id, second_post_id, other_post_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    start = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)
    end = datetime(2026, 4, 4, 12, tzinfo=timezone.utc)
    with engine.connect() as connection:
        # SQLite has no PostgreSQL timezone function; fixture timestamps are UTC.
        connection.connection.driver_connection.create_function(
            "timezone", 2, lambda zone, timestamp: timestamp
        )
        connection.execute(posts.insert(), [
            {"id": post_id, "user_id": creator_id},
            {"id": second_post_id, "user_id": creator_id},
            {"id": other_post_id, "user_id": other_creator_id},
        ])
        connection.execute(comments.insert(), [
            {
                "id": uuid.uuid4(), "post_id": owner_post,
                "created_at": created_at, "deleted_at": deleted_at,
            }
            for owner_post, created_at, deleted_at in [
                (post_id, start - timedelta(microseconds=1), None),
                (post_id, start, None),
                (post_id, start + timedelta(hours=12), None),
                (second_post_id, start + timedelta(hours=13), None),
                (post_id, end, None),
                (post_id, end + timedelta(microseconds=1), None),
                (post_id, start + timedelta(hours=14), end),
                (other_post_id, start + timedelta(hours=15), None),
            ]
        ])

        async def execute(statement):
            return connection.execute(statement)

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        yield db, creator_id, start, end
    engine.dispose()


@pytest.mark.asyncio
async def test_daily_comments_match_period_total(comment_database):
    db, creator_id, start, end = comment_database
    repository = CommentRepository(db)

    rows = await repository.daily_counts_for_creator(creator_id, start, end)
    total = await repository.count_for_creator(creator_id, start, end)

    assert rows == [
        {"date": "2026-04-01", "comments": 1},
        {"date": "2026-04-02", "comments": 2},
        {"date": "2026-04-04", "comments": 1},
    ]
    assert sum(row["comments"] for row in rows) == total == 4
    statement = db.execute.await_args_list[0].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "date(timezone(" in str(compiled)
    assert "UTC" in compiled.params.values()
    assert "JOIN posts ON comments.post_id = posts.id" in str(compiled)
    assert "comments.deleted_at IS NULL" in str(compiled)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_hours", [0, -7, 14])
async def test_creator_comment_trends_zero_fill_and_use_utc_days(
    comment_database, monkeypatch, offset_hours
):
    db, creator_id, start, end = comment_database
    service = CreatorAnalyticsService(db)
    monkeypatch.setattr(
        service.analytics_repo, "daily_signal_totals",
        AsyncMock(return_value=[{"date": "2026-04-02", "views": 5, "comments": 0}]),
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
        AsyncMock(return_value=[]),
    )
    requested_timezone = timezone(timedelta(hours=offset_hours))
    rows = await service.daily_trends(
        creator_id, start.astimezone(requested_timezone), end.astimezone(requested_timezone)
    )

    assert [row["date"] for row in rows] == [
        "2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04",
    ]
    assert [row["comments"] for row in rows] == [1, 2, 0, 1]
    assert rows[1]["views"] == 5
    assert rows[2]["shares"] == 0
    assert sum(row["comments"] for row in rows) == await CommentRepository(
        db
    ).count_for_creator(creator_id, start, end)


@pytest.mark.asyncio
async def test_creator_without_comments_has_zero_filled_trends(comment_database, monkeypatch):
    db, _, start, end = comment_database
    service = CreatorAnalyticsService(db)
    monkeypatch.setattr(service.analytics_repo, "daily_signal_totals", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_for_user_in_period",
        AsyncMock(return_value=[]),
    )

    rows = await service.daily_trends(uuid.uuid4(), start, end)

    assert len(rows) == 4
    assert all(row["comments"] == 0 for row in rows)
