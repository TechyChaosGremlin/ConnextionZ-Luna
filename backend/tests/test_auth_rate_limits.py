"""Focused integration tests for REST authentication endpoint rate limits."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.dependencies import get_db_session
from app.errors import register_exception_handlers
from app.main import RateLimitMiddleware
from app.models.user import AccountStatus
from app.rate_limits import RATE_LIMIT_MESSAGE
from features.auth import router as auth_router
from features.auth.jwt import ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE

USER_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def auth_harness(monkeypatch):
    user = SimpleNamespace(
        id=USER_ID,
        status=AccountStatus.ACTIVE,
        hashed_password="hashed-password",
    )
    repository = Mock()
    repository.get_by_email = AsyncMock(return_value=user)
    repository.get_by_username = AsyncMock(return_value=None)
    repository.get_by_id = AsyncMock(return_value=user)

    async def create_user(new_user):
        new_user.id = uuid4()

    repository.create = AsyncMock(side_effect=create_user)
    monkeypatch.setattr(auth_router, "UserRepository", lambda _db: repository)
    monkeypatch.setattr(auth_router, "check_password_strength", lambda _password: (True, []))
    monkeypatch.setattr(auth_router, "hash_password", lambda _password: "hashed-password")
    monkeypatch.setattr(
        auth_router,
        "verify_password",
        lambda password, _hashed_password: password == "correct-password",
    )
    monkeypatch.setattr(
        auth_router, "create_access_token", lambda _user: "access-token"
    )
    monkeypatch.setattr(
        auth_router, "create_refresh_token", lambda _user: "refresh-token"
    )

    def decode_token(token):
        if token == "refresh-token":
            return {"type": REFRESH_TOKEN_TYPE, "sub": str(USER_ID)}
        return {
            "type": ACCESS_TOKEN_TYPE,
            "jti": "logout-jti",
            "exp": 2_000_000_000,
        }

    monkeypatch.setattr("features.auth.jwt.decode_token", decode_token)
    monkeypatch.setattr(
        "features.auth.jwt.blacklist_token", AsyncMock(return_value=True)
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(auth_router.router)

    db = AsyncMock()

    async def get_db():
        return db

    app.dependency_overrides[get_db_session] = get_db
    return SimpleNamespace(app=app, user=user, repository=repository)


@pytest_asyncio.fixture
async def client(auth_harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=auth_harness.app, client=("192.0.2.10", 1234)
        ),
        base_url="http://test",
    ) as test_client:
        yield test_client


def assert_rate_limited(response, retry_after_limit=60):
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "TOO_MANY_REQUESTS",
            "message": RATE_LIMIT_MESSAGE,
        }
    }
    assert 1 <= int(response.headers["Retry-After"]) <= retry_after_limit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "query", "body"),
    [
        (
            "/auth/register",
            {"email": "url@example.test", "password": "url-secret"},
            {"email": "body@example.test", "username": "body-user", "password": "body-secret"},
        ),
        (
            "/auth/login",
            {"email": "url@example.test", "password": "url-secret"},
            {"email": "body@example.test", "password": "body-secret"},
        ),
        (
            "/auth/refresh",
            {"refresh_token": "url-secret"},
            {"refresh_token": "body-secret"},
        ),
        (
            "/auth/password-reset/confirm",
            {"token": "url-secret", "new_password": "url-secret"},
            {"token": "body-token", "new_password": "body-password"},
        ),
    ],
)
async def test_auth_values_in_query_are_rejected(client, path, query, body):
    response = await client.post(path, params=query, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == (
        "Authentication values must be sent in the request body"
    )
    assert "url-secret" not in response.text


@pytest.mark.asyncio
async def test_legacy_query_only_login_is_not_accepted(client, auth_harness):
    response = await client.post(
        "/auth/login",
        params={"email": "url@example.test", "password": "url-secret"},
    )

    assert response.status_code == 422
    auth_harness.repository.get_by_email.assert_not_awaited()


async def post_login(client, email="user@example.com", password="correct-password"):
    return await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )


async def post_register(client, suffix="user"):
    return await client.post(
        "/auth/register",
        json={
            "email": f"{suffix}@example.com",
            "username": suffix,
            "password": "Strong-password1!",
        },
    )


@pytest.mark.asyncio
async def test_login_within_limit_succeeds_and_preserves_response(client):
    response = await post_login(client)

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "token_type": "bearer",
        "expires_in": auth_router.settings.jwt_access_token_expire_minutes * 60,
    }


@pytest.mark.asyncio
async def test_repeated_failed_logins_eventually_return_429(client, auth_harness):
    auth_harness.repository.get_by_email.return_value = None

    for _ in range(10):
        response = await post_login(client, password="wrong-password")
        assert response.status_code == 401

    assert_rate_limited(await post_login(client, password="wrong-password"))
    assert auth_harness.repository.get_by_email.await_count == 10


@pytest.mark.asyncio
async def test_register_has_its_own_tighter_ip_bucket(client, auth_harness):
    auth_harness.repository.get_by_email.return_value = None

    for index in range(5):
        response = await post_register(client, f"new-user-{index}")
        assert response.status_code == 201
        assert response.json()["status"] == AccountStatus.ACTIVE.value

    assert_rate_limited(await post_register(client, "new-user-over-limit"), 60 * 60)
    assert auth_harness.repository.create.await_count == 5


@pytest.mark.asyncio
async def test_refresh_has_a_separate_endpoint_bucket(client, auth_harness):
    for _ in range(30):
        response = await client.post(
            "/auth/refresh", json={"refresh_token": "refresh-token"}
        )
        assert response.status_code == 200
        assert response.json() == {
            "access_token": "access-token",
            "token_type": "bearer",
            "expires_in": auth_router.settings.jwt_access_token_expire_minutes * 60,
        }

    assert_rate_limited(
        await client.post("/auth/refresh", json={"refresh_token": "refresh-token"})
    )
    assert auth_harness.repository.get_by_id.await_count == 30


@pytest.mark.asyncio
async def test_logout_has_its_own_endpoint_bucket(client):
    for _ in range(30):
        response = await client.post(
            "/auth/logout", headers={"Authorization": "Bearer access-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Logged out successfully"}

    assert_rate_limited(
        await client.post(
            "/auth/logout", headers={"Authorization": "Bearer access-token"}
        )
    )


@pytest.mark.asyncio
async def test_auth_endpoints_do_not_consume_each_others_buckets(client, auth_harness):
    auth_harness.repository.get_by_email.return_value = None
    for _ in range(10):
        assert (await post_login(client, password="wrong-password")).status_code == 401

    assert_rate_limited(await post_login(client, password="wrong-password"))
    assert (await post_register(client, "independent")).status_code == 201
    assert (
        await client.post("/auth/refresh", json={"refresh_token": "refresh-token"})
    ).status_code == 200
    assert (
        await client.post(
            "/auth/logout", headers={"Authorization": "Bearer access-token"}
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_successful_auth_responses_remain_unchanged(client, auth_harness):
    auth_harness.repository.get_by_email.return_value = None
    registration = await post_register(client, "response-shape")
    assert registration.status_code == 201
    assert set(registration.json()) == {"message", "user_id", "status"}
    assert registration.json()["message"] == "User registered successfully"

    auth_harness.repository.get_by_email.return_value = auth_harness.user
    login = await post_login(client)
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"

    refresh = await client.post(
        "/auth/refresh", json={"refresh_token": "refresh-token"}
    )
    assert refresh.status_code == 200
    assert set(refresh.json()) == {"access_token", "token_type", "expires_in"}

    logout = await client.post(
        "/auth/logout", headers={"Authorization": "Bearer access-token"}
    )
    assert logout.status_code == 200
    assert logout.json() == {"message": "Logged out successfully"}


@pytest.mark.asyncio
async def test_existing_global_ip_limiter_still_allows_60_requests(client, auth_harness):
    @auth_harness.app.get("/rate-limit-probe")
    async def rate_limit_probe():
        return {"ok": True}

    for _ in range(60):
        response = await client.get("/rate-limit-probe")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "60"

    assert_rate_limited(await client.get("/rate-limit-probe"))
