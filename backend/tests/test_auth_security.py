from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis.exceptions
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api.graphql import _graphql_user_from_token
from features.auth.jwt import blacklist_token, is_token_blacklisted
from features.auth.router import logout
from repositories.user_repository import UserRepository


@pytest.mark.asyncio
async def test_blacklist_token_marks_jti():
    try:
        from services.redis_service import RedisService
        redis = RedisService()
        await redis.connect()
        await redis.disconnect()
    except Exception:
        pytest.skip("Redis is not running in this environment; skipping token blacklist integration test.")

    jti = "test-jti-123"
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)

    await blacklist_token(jti, exp)

    assert await is_token_blacklisted(jti) is True


@pytest.mark.asyncio
async def test_blacklist_token_ignores_expired_token():
    try:
        from services.redis_service import RedisService
        redis = RedisService()
        await redis.connect()
        await redis.disconnect()
    except Exception:
        pytest.skip("Redis is not running in this environment; skipping token blacklist integration test.")

    jti = "expired-jti-456"
    exp = datetime.now(timezone.utc) - timedelta(minutes=1)

    await blacklist_token(jti, exp)

    assert await is_token_blacklisted(jti) is False


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
