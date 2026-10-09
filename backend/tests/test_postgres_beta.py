"""PostgreSQL-only beta lifecycle and persistent revocation contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as main
from api.graphql import _graphql_user_from_token
from app.config import settings
from app.models.token_revocation import TokenRevocation
from app.models.user import AccountStatus, User, UserRole
from features.auth.jwt import (
    blacklist_token,
    create_access_token,
    is_token_blacklisted,
)
from features.auth.middleware import get_current_user
from features.auth.router import logout
from repositories.user_repository import UserRepository
from services import token_revocation_store as store
from services.rabbitmq_service import RabbitMQService


@pytest_asyncio.fixture
async def revocations(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'revocations.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(TokenRevocation.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(store, "async_session_factory", sessions)
    monkeypatch.setattr(settings, "infrastructure_mode", "postgres_beta")
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revocations_persist_across_sessions_and_duplicate_logout(revocations):
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert await is_token_blacklisted("beta-jti") is False
    assert await blacklist_token("beta-jti", expires) is True
    assert await blacklist_token("beta-jti", expires) is True
    assert await is_token_blacklisted("beta-jti") is True
    async with revocations() as session:
        assert await session.scalar(select(func.count()).select_from(TokenRevocation)) == 1


@pytest.mark.asyncio
async def test_concurrent_revocations_do_not_shorten_expiry(revocations):
    now = datetime.now(timezone.utc)
    assert await asyncio.gather(
        blacklist_token("shared-jti", now + timedelta(minutes=1)),
        blacklist_token("shared-jti", now + timedelta(minutes=5)),
    ) == [True, True]
    async with revocations() as session:
        expiry = await session.scalar(select(TokenRevocation.expires_at))
        assert expiry.replace(tzinfo=timezone.utc) == now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_expired_revocations_are_ignored_and_cleaned_up(revocations):
    now = datetime.now(timezone.utc)
    async with revocations() as session:
        session.add(TokenRevocation(jti="old-jti", expires_at=now - timedelta(minutes=1)))
        await session.commit()
    assert await is_token_blacklisted("old-jti") is False
    assert await blacklist_token("already-expired", now - timedelta(minutes=1)) is True
    assert await blacklist_token("current-jti", now + timedelta(minutes=5)) is True
    async with revocations() as session:
        assert (await session.scalars(select(TokenRevocation.jti))).all() == ["current-jti"]


@pytest.mark.asyncio
async def test_logout_revokes_rest_and_graphql_access_without_redis(revocations, monkeypatch):
    user = User(
        id=uuid.uuid4(), email="beta@example.test", username="beta",
        role=UserRole.USER, status=AccountStatus.ACTIVE,
    )
    token = create_access_token(user)
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(UserRepository, "get_by_id", lookup)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert await get_current_user(credentials, AsyncMock()) is user
    assert (await _graphql_user_from_token(AsyncMock(), token))[0] is user
    result = await logout(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), AsyncMock()
    )
    assert result == {"message": "Logged out successfully"}
    lookup.reset_mock()
    with pytest.raises(HTTPException) as error:
        await get_current_user(credentials, AsyncMock())
    assert error.value.status_code == 401
    assert await _graphql_user_from_token(AsyncMock(), token) == (None, None)
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_failure_fails_closed_and_logout_returns_503(revocations, monkeypatch):
    user = User(
        id=uuid.uuid4(), email="beta@example.test", username="beta",
        role=UserRole.USER, status=AccountStatus.ACTIVE,
    )
    token = create_access_token(user)
    def unavailable():
        raise RuntimeError("sensitive-connection-marker")
    monkeypatch.setattr(store, "async_session_factory", unavailable)
    logger = Mock()
    monkeypatch.setattr(store, "logger", logger)
    with pytest.raises(RuntimeError, match="revocation store is unavailable"):
        await is_token_blacklisted("jti")
    assert await _graphql_user_from_token(AsyncMock(), token) == (None, None)
    with pytest.raises(HTTPException) as error:
        await get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), AsyncMock()
        )
    assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        await logout(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), AsyncMock()
        )
    assert error.value.status_code == 503
    assert "sensitive-connection-marker" not in str(logger.mock_calls)


@pytest.mark.asyncio
async def test_beta_lifecycle_and_readiness_do_not_contact_external_services(
    revocations, monkeypatch,
):
    monkeypatch.setattr(main, "configure_logging", Mock())
    cleanup = AsyncMock()
    monkeypatch.setattr(main.stream_manager, "cleanup", cleanup)
    redis = Mock(side_effect=AssertionError("Redis must not be instantiated"))
    broker = AsyncMock()
    monkeypatch.setattr(main, "RedisService", redis)
    monkeypatch.setattr(main, "RabbitMQService", redis)
    monkeypatch.setattr(main.rabbitmq_service, "connect", broker)
    monkeypatch.setattr(main, "check_db_connection", AsyncMock(return_value=True))
    app = main.create_app()
    async with main.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "token_revocations": "ok"},
        "infrastructure_mode": "postgres_beta",
        "background_queue": "disabled",
    }
    redis.assert_not_called()
    broker.assert_not_awaited()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_beta_requires_revocation_schema_at_startup_and_readiness(
    revocations, monkeypatch,
):
    monkeypatch.setattr(main, "configure_logging", Mock())
    monkeypatch.setattr(main.stream_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(main, "check_db_connection", AsyncMock(return_value=True))
    monkeypatch.setattr(
        main, "check_revocation_store", AsyncMock(side_effect=RuntimeError("missing schema")),
    )
    app = main.create_app()
    with pytest.raises(RuntimeError, match="missing schema"):
        async with main.lifespan(app):
            pytest.fail("Started without revocation schema")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["token_revocations"] == "error"


@pytest.mark.asyncio
async def test_beta_rejects_broker_connections(revocations):
    with pytest.raises(RuntimeError, match="Background queues are disabled"):
        await RabbitMQService().connect()
