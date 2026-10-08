"""PostgreSQL deletion checks for onboarding profile categories."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, Table, delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from api.graphql import AppContext, _delete_account
from app.models.category import Category, profile_categories
from app.models.streaming import (
    ConnectedStreamAccount,
    StreamPlatform,
    StreamSession,
    StreamSessionStatus,
)
from app.models.user import AccountStatus, Profile, Session as AuthSession, User, UserRole
from repositories.category_repository import CategoryRepository


@pytest_asyncio.fixture
async def onboarding_store():
    url = os.environ.get("ONBOARDING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set ONBOARDING_TEST_DATABASE_URL to an isolated PostgreSQL database")

    schema = f"onboarding_deletion_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    metadata = MetaData()
    for table in (
        User.__table__,
        Profile.__table__,
        AuthSession.__table__,
        StreamSession.__table__,
        ConnectedStreamAccount.__table__,
        Category.__table__,
        profile_categories,
    ):
        assert isinstance(table, Table)
        table.to_metadata(metadata)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield engine, sessions
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


def _make_user(user_id: uuid.UUID) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.test",
        username=f"onboarding_{user_id.hex[:12]}",
        hashed_password="test",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )


@pytest.mark.asyncio
async def test_account_profile_and_category_deletions_cascade_and_deleted_users_are_excluded(
    onboarding_store,
):
    _engine, sessions = onboarding_store
    records: dict[str, tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = {}

    async with sessions() as session:
        for name in ("account", "profile", "category", "soft_profile", "soft_user"):
            user_id, profile_id, category_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            user = _make_user(user_id)
            category = Category(id=category_id, name=name, slug=name)
            profile = Profile(
                id=profile_id,
                user_id=user_id,
                display_name=name,
                response_time="< 1 hour",
                open_to_collab=False,
                onboarding_collab_types=["Brand Deal"],
                categories=[category],
            )
            if name == "soft_profile":
                profile.deleted_at = datetime.now(timezone.utc)
            if name == "soft_user":
                user.deleted_at = datetime.now(timezone.utc)
            session.add_all([user, profile])
            records[name] = (user_id, profile_id, category_id)
            if name == "account":
                session.add_all(
                    [
                        AuthSession(
                            user_id=user_id,
                            refresh_token_jti=f"refresh-{user_id}",
                            expires_at=datetime.now(timezone.utc),
                        ),
                        StreamSession(
                            owner_id=user_id,
                            input_source="rtmp://example.test/live",
                            status=StreamSessionStatus.ENDED,
                        ),
                        ConnectedStreamAccount(
                            user_id=user_id,
                            platform=StreamPlatform.TWITCH,
                            platform_user_id="test-account",
                        ),
                    ]
                )
        await session.commit()

    async with sessions() as session:
        user_id, _, _ = records["account"]
        user = (
            await session.execute(
                select(User)
                .options(
                    selectinload(User.sessions),
                    selectinload(User.profile).selectinload(Profile.categories),
                )
                .where(User.id == user_id)
            )
        ).scalar_one()
        await _delete_account(AppContext(session, user))

    async with sessions() as session:
        assert (
            await session.execute(select(func.count()).select_from(User).where(User.id == records["account"][0]))
        ).scalar_one() == 0
        assert (
            await session.execute(select(func.count()).select_from(Profile).where(Profile.id == records["account"][1]))
        ).scalar_one() == 0
        for child_model, user_column in (
            (AuthSession, AuthSession.user_id),
            (StreamSession, StreamSession.owner_id),
            (ConnectedStreamAccount, ConnectedStreamAccount.user_id),
        ):
            assert (
                await session.execute(
                    select(func.count()).select_from(child_model).where(user_column == records["account"][0])
                )
            ).scalar_one() == 0
        assert (
            await session.execute(
                select(func.count())
                .select_from(profile_categories)
                .where(profile_categories.c.category_id == records["account"][2])
            )
        ).scalar_one() == 0

        assert await CategoryRepository(session).get_slugs_by_user_id(records["soft_profile"][0]) == []
        assert await CategoryRepository(session).get_slugs_by_user_id(records["soft_user"][0]) == []

        await session.execute(delete(Profile.__table__).where(Profile.id == records["profile"][1]))
        await session.flush()
        assert (
            await session.execute(
                select(func.count())
                .select_from(profile_categories)
                .where(profile_categories.c.profile_id == records["profile"][1])
            )
        ).scalar_one() == 0

        await session.execute(delete(Category.__table__).where(Category.id == records["category"][2]))
        await session.flush()
        assert (
            await session.execute(
                select(func.count())
                .select_from(profile_categories)
                .where(profile_categories.c.category_id == records["category"][2])
            )
        ).scalar_one() == 0
