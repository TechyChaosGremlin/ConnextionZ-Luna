from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.dependencies import get_db_session
from app.errors import register_exception_handlers
from app.models.user import AccountStatus, User, UserRole
from features.auth import middleware
from features.auth.jwt import create_access_token, create_refresh_token, decode_token
from features.auth.router import RefreshRequest, refresh
from features.media.router import router as media_router


@pytest.fixture
def active_user():
    return User(
        id=uuid4(),
        email="token-boundary@example.test",
        username="token_boundary",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_rest_auth_rejects_refresh_token_before_user_lookup(monkeypatch, active_user):
    lookup = AsyncMock(return_value=active_user)
    blacklist = AsyncMock(return_value=False)
    monkeypatch.setattr(middleware.UserRepository, "get_by_id", lookup)
    monkeypatch.setattr(middleware, "is_token_blacklisted", blacklist)

    with pytest.raises(HTTPException) as error:
        await middleware.get_current_user(
            HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=create_refresh_token(active_user)
            ),
            AsyncMock(),
        )

    assert error.value.status_code == 401
    lookup.assert_not_awaited()
    blacklist.assert_not_awaited()


@pytest.mark.asyncio
async def test_rest_auth_accepts_active_access_token(monkeypatch, active_user):
    monkeypatch.setattr(
        middleware.UserRepository, "get_by_id", AsyncMock(return_value=active_user)
    )
    blacklist = AsyncMock(return_value=False)
    monkeypatch.setattr(middleware, "is_token_blacklisted", blacklist)

    user = await middleware.get_current_user(
        HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=create_access_token(active_user)
        ),
        AsyncMock(),
    )

    assert await middleware.get_current_active_user(user) is active_user
    blacklist.assert_awaited_once()


@pytest.mark.asyncio
async def test_rest_auth_rejects_expired_access_token(monkeypatch, active_user):
    lookup = AsyncMock(return_value=active_user)
    monkeypatch.setattr(middleware.UserRepository, "get_by_id", lookup)

    with pytest.raises(HTTPException) as error:
        await middleware.get_current_user(
            HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials=create_access_token(active_user, timedelta(seconds=-1)),
            ),
            AsyncMock(),
        )

    assert error.value.status_code == 401
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_token_cannot_reach_protected_media_endpoint(monkeypatch, active_user):
    lookup = AsyncMock(return_value=active_user)
    monkeypatch.setattr(middleware.UserRepository, "get_by_id", lookup)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(media_router)

    async def get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db_session] = get_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete(
            f"/media/{uuid4()}",
            headers={"Authorization": f"Bearer {create_refresh_token(active_user)}"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedicated_refresh_flow_still_issues_access_token(monkeypatch, active_user):
    monkeypatch.setattr(
        middleware.UserRepository, "get_by_id", AsyncMock(return_value=active_user)
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "headers": [],
            "query_string": b"",
        }
    )
    result = await refresh(
        RefreshRequest(refresh_token=create_refresh_token(active_user)),
        request,
        AsyncMock(),
    )

    payload = decode_token(result["access_token"])
    assert payload["type"] == "access"
    assert payload["sub"] == str(active_user.id)
    assert set(result) == {"access_token", "token_type", "expires_in"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [AccountStatus.SUSPENDED, AccountStatus.BANNED, AccountStatus.PENDING_VERIFICATION],
)
async def test_active_dependency_still_rejects_inactive_users(active_user, status):
    active_user.status = status
    with pytest.raises(HTTPException) as error:
        await middleware.get_current_active_user(active_user)
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("store_unavailable", [False, True])
async def test_rest_auth_fails_closed_for_revoked_or_uncheckable_tokens(
    monkeypatch, active_user, store_unavailable
):
    lookup = AsyncMock(return_value=active_user)
    blacklist = AsyncMock(
        return_value=True,
        side_effect=RuntimeError("revocation store unavailable") if store_unavailable else None,
    )
    monkeypatch.setattr(middleware.UserRepository, "get_by_id", lookup)
    monkeypatch.setattr(middleware, "is_token_blacklisted", blacklist)

    with pytest.raises(HTTPException) as error:
        await middleware.get_current_user(
            HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=create_access_token(active_user)
            ),
            AsyncMock(),
        )

    assert error.value.status_code == 401
    lookup.assert_not_awaited()
