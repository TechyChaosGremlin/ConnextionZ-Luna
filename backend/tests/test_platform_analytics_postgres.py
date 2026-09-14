from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.graphql import AppContext, _platform_analytics, _platform_top_content
from app.models.analytics import AnalyticsEvent, EventType
from app.models.base import Base
from app.models.content import ContentStatus, ContentType, Post
from app.models.user import AccountStatus, User, UserRole


DATABASE_URL = os.getenv("ANALYTICS_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="Set ANALYTICS_TEST_DATABASE_URL to run live Postgres analytics validation",
    ),
]


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
