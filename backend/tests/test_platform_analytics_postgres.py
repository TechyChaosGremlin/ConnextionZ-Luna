from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.graphql import AppContext, _platform_analytics, _platform_top_content, _track_post_watch
from app.models.analytics import AnalyticsEvent, EventType, InteractionSignal, SignalType
from app.models.base import Base
from app.models.content import ContentStatus, ContentType, Post
from app.models.user import AccountStatus, User, UserRole
from services.creator_analytics_service import CreatorAnalyticsService


DATABASE_URL = os.getenv("ANALYTICS_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="Set ANALYTICS_TEST_DATABASE_URL to run live Postgres analytics validation",
    ),
]


@pytest.fixture
def published_video_factory():
    async def create(session):
        suffix = uuid.uuid4().hex
        creator = User(
            email=f"analytics-creator-{suffix}@example.test",
            username=f"analytics_creator_{suffix[:12]}",
            hashed_password="integration-test-only",
            role=UserRole.CREATOR,
            status=AccountStatus.ACTIVE,
            email_verified=True,
        )
        viewer = User(
            email=f"analytics-viewer-{suffix}@example.test",
            username=f"analytics_viewer_{suffix[:12]}",
            hashed_password="integration-test-only",
            status=AccountStatus.ACTIVE,
            email_verified=True,
        )
        session.add_all([creator, viewer])
        await session.flush()

        post = Post(
            user_id=creator.id,
            content_type=ContentType.VIDEO,
            status=ContentStatus.PUBLISHED,
            caption="Published video watch analytics integration post",
            duration_sec=120.0,
            published_at=datetime.now(timezone.utc).isoformat(),
            audio="Original Sound",
            moderation_status="approved",
        )
        session.add(post)
        await session.flush()
        return creator, viewer, post

    return create


async def test_admin_platform_analytics_against_postgres():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(minutes=1)

    async with session_factory() as session:
        async with session.begin():
            admin = User(
                email=f"analytics-admin-{uuid.uuid4()}@example.test",
                username=f"analytics_admin_{uuid.uuid4().hex[:12]}",
                hashed_password="integration-test-only",
                role=UserRole.ADMIN,
                status=AccountStatus.ACTIVE,
            )
            await session.flush()
            post = Post(
                user_id=admin.id,
                content_type=ContentType.VIDEO,
                status=ContentStatus.PUBLISHED,
                caption="Analytics integration post",
                audio="Original Sound",
                moderation_status="approved",
            )
            session.add(post)
            await session.flush()
            session.add_all([
                AnalyticsEvent(
                    user_id=admin.id,
                    post_id=post.id,
                    event_type=EventType.VIDEO_VIEWED,
                    created_at=now,
                ),
                AnalyticsEvent(
                    user_id=admin.id,
                    post_id=post.id,
                    event_type=EventType.LIKE_CREATED,
                    created_at=now,
                ),
                AnalyticsEvent(
                    user_id=admin.id,
                    post_id=post.id,
                    event_type=EventType.VIDEO_PUBLISHED,
                    created_at=now,
                ),
            ])
            await session.flush()

            context = AppContext(
                db=session,
                current_user=SimpleNamespace(id=admin.id, role=UserRole.ADMIN),
            )
            period = SimpleNamespace(start=start, end=end)
            overview = await _platform_analytics(context, period)
            top_content = await _platform_top_content(context, period, "engagement",)

            assert overview.total_views == 1
            assert overview.total_likes == 1
            assert overview.active_users == 1
            assert top_content[0].post.id == post.id
            assert top_content[0].engagement_rate == 100.0

    await engine.dispose()


async def test_published_video_watch_persists_non_stream_signals_for_creator_analytics(
    published_video_factory,
):
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    end = start + timedelta(minutes=2)

    try:
        async with session_factory() as session:
            creator, viewer, post = await published_video_factory(session)
            assert post.content_type == ContentType.VIDEO
            assert post.status == ContentStatus.PUBLISHED
            assert post.duration_sec == 120.0

            result = await _track_post_watch(
                AppContext(db=session, current_user=viewer),
                post.id,
                watched_seconds=110.0,
                completed=True,
            )
            assert result.completed is True

            signals = list(
                (
                    await session.execute(
                        select(InteractionSignal).where(InteractionSignal.post_id == post.id)
                    )
                )
                .scalars()
                .all()
            )
            signals_by_type = {signal.signal_type: signal for signal in signals}
            assert set(signals_by_type) == {
                SignalType.VIEW,
                SignalType.WATCH_DURATION,
                SignalType.COMPLETION,
            }
            assert len(signals) == 3
            for signal in signals:
                assert signal.user_id == viewer.id
                assert signal.creator_id == creator.id
                assert signal.post_id == post.id
                assert signal.stream_session_id is None
            assert signals_by_type[SignalType.WATCH_DURATION].value == pytest.approx(110.0)

            totals = await CreatorAnalyticsService(session)._period_totals(
                creator.id, start, end
            )
            assert totals["views"] == 1
            assert totals["watch_sec"] == pytest.approx(110.0)
            assert totals["completions"] == 1
            assert totals["completion_rate"] == pytest.approx(100.0)
    finally:
        await engine.dispose()
