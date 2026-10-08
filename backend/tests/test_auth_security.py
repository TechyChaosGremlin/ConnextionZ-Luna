from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
import redis.exceptions
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api.graphql import _graphql_user_from_token
from app.models.user import AccountStatus, User, UserRole
from features.auth.jwt import (
    blacklist_token,
    create_access_token,
    decode_token,
    is_token_blacklisted,
)
from features.auth.router import logout
from repositories.user_repository import UserRepository
from services.redis_service import RedisService


@pytest_asyncio.fixture
async def live_redis():
    service = RedisService()
    try:
        try:
            await service.connect()
            assert service.redis is not None
            await service.redis.ping()
        except redis.exceptions.AuthenticationError:
            raise
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            pytest.skip("Configured Redis is unreachable; token blacklist integration requires Redis.")
        yield service
    finally:
        if service.redis is not None:
            await service.disconnect()


@pytest.mark.asyncio
async def test_redis_disconnect_uses_supported_async_close():
    service = RedisService()
    service.redis = AsyncMock()

    await service.disconnect()

    service.redis.aclose.assert_awaited_once()
    service.redis.close.assert_not_called()


@pytest.mark.asyncio
async def test_blacklist_token_marks_jti(live_redis):
    jti = f"alpha-blacklist-{uuid.uuid4().hex}"
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)

    try:
        assert await blacklist_token(jti, exp) is True
        assert await is_token_blacklisted(jti) is True
        assert live_redis.redis is not None
        assert 0 < await live_redis.redis.ttl(f"blacklist:{jti}") <= 300
    finally:
        assert live_redis.redis is not None
        await live_redis.redis.delete(f"blacklist:{jti}")


@pytest.mark.asyncio
async def test_blacklist_token_ignores_expired_token(live_redis):
    jti = f"alpha-expired-{uuid.uuid4().hex}"
    exp = datetime.now(timezone.utc) - timedelta(minutes=1)

    try:
        assert await blacklist_token(jti, exp) is True
        assert await is_token_blacklisted(jti) is False
        assert live_redis.redis is not None
        assert await live_redis.redis.exists(f"blacklist:{jti}") == 0
    finally:
        assert live_redis.redis is not None
        await live_redis.redis.delete(f"blacklist:{jti}")


@pytest.mark.asyncio
async def test_live_redis_logout_revokes_graphql_access(monkeypatch, live_redis):
    user = User(
        id=uuid.uuid4(),
        email="alpha-redis@example.test",
        username="alpha_redis",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
    )
    token = create_access_token(user, expires_delta=timedelta(minutes=5))
    jti = decode_token(token)["jti"]
    user_lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(UserRepository, "get_by_id", user_lookup)

    try:
        authenticated_user, _session_id = await _graphql_user_from_token(AsyncMock(), token)
        assert authenticated_user is user
        user_lookup.reset_mock()

        result = await logout(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            AsyncMock(),
        )

        assert result == {"message": "Logged out successfully"}
        assert await is_token_blacklisted(jti) is True
        assert await _graphql_user_from_token(AsyncMock(), token) == (None, None)
        user_lookup.assert_not_awaited()
    finally:
        assert live_redis.redis is not None
        await live_redis.redis.delete(f"blacklist:{jti}")


@pytest.mark.asyncio
async def test_logout_blacklists_access_token(monkeypatch):
    token_id = "logout-test-jti"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    expiry_timestamp = expires_at.timestamp()
    blacklist = AsyncMock()
    monkeypatch.setattr(
        "features.auth.jwt.decode_token",
        lambda token: {"type": "access", "jti": token_id, "exp": expiry_timestamp},
    )
    monkeypatch.setattr("features.auth.jwt.blacklist_token", blacklist)

    result = await logout(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token"),
        AsyncMock(),
    )

    assert result == {"message": "Logged out successfully"}
    blacklist.assert_awaited_once()
    assert blacklist.await_args.args[0] == token_id
    assert blacklist.await_args.args[1].timestamp() == pytest.approx(expiry_timestamp)


@pytest.mark.asyncio
async def test_logout_reports_revocation_store_failure(monkeypatch):
    monkeypatch.setattr(
        "features.auth.jwt.decode_token",
        lambda token: {"type": "access", "jti": "unavailable-store-jti", "exp": 2_000_000_000},
    )
    monkeypatch.setattr("features.auth.jwt.blacklist_token", AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as error:
        await logout(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token"),
            AsyncMock(),
        )

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_graphql_context_rejects_revoked_access_token(monkeypatch):
    token_id = "revoked-graphql-jti"
    user_lookup = AsyncMock(return_value=SimpleNamespace(id="user-id"))
    monkeypatch.setattr(
        "features.auth.jwt.decode_token",
        lambda token: {"type": "access", "jti": token_id, "sub": "user-id"},
    )
    monkeypatch.setattr("features.auth.jwt.is_token_blacklisted", AsyncMock(return_value=True))
    monkeypatch.setattr(UserRepository, "get_by_id", user_lookup)

    user, session_id = await _graphql_user_from_token(AsyncMock(), "revoked-token")

    assert user is None
    assert session_id is None
    user_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_graphql_context_fails_closed_when_revocation_store_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "features.auth.jwt.decode_token",
        lambda token: {"type": "access", "jti": "unavailable-store-jti", "sub": "user-id"},
    )

    async def unavailable(_token_id):
        raise RuntimeError("redis offline")

    monkeypatch.setattr("features.auth.jwt.is_token_blacklisted", unavailable)

    user, session_id = await _graphql_user_from_token(AsyncMock(), "valid-token")

    assert user is None
    assert session_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [AccountStatus.SUSPENDED, AccountStatus.BANNED, AccountStatus.PENDING_VERIFICATION],
)
async def test_graphql_context_rejects_non_active_accounts(monkeypatch, status):
    user = SimpleNamespace(id="user-id", status=status)
    monkeypatch.setattr(
        "features.auth.jwt.decode_token",
        lambda token: {"type": "access", "jti": "inactive-user-jti", "sub": "user-id"},
    )
    monkeypatch.setattr("features.auth.jwt.is_token_blacklisted", AsyncMock(return_value=False))
    monkeypatch.setattr(UserRepository, "get_by_id", AsyncMock(return_value=user))

    authenticated_user, session_id = await _graphql_user_from_token(
        AsyncMock(),
        "valid-but-inactive-account-token",
    )

    assert authenticated_user is None
    assert session_id is None
