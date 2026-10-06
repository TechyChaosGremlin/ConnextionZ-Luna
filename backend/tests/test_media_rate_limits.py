"""Focused tests for media upload frequency and cumulative-volume limits."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request

import app.main as app_main
import features.media.router as media_router
from app.dependencies import get_db_session
from app.errors import register_exception_handlers
from app.main import RateLimitMiddleware
from app.models.user import UserRole
from app.rate_limits import (
    RATE_LIMIT_MESSAGE,
    UPLOAD_VOLUME_LIMITS,
    ActionLimit,
    ActionRateLimiter,
)
from features.auth.middleware import get_current_active_user
from services.media_storage import StoredMedia

POST_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")
USER_ONE = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_TWO = uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def upload_harness(monkeypatch):
    users = {
        "user-one": SimpleNamespace(id=USER_ONE, role=UserRole.USER),
        "user-two": SimpleNamespace(id=USER_TWO, role=UserRole.ADMIN),
    }
    monkeypatch.setattr(
        app_main,
        "decode_token",
        lambda token: {"type": "access", "sub": str(users[token].id)},
    )

    async def current_user(request: Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in users:
            raise HTTPException(status_code=401, detail="Authentication required")
        return users[token]

    monkeypatch.setattr(media_router, "upload_volume_limiter", ActionRateLimiter(UPLOAD_VOLUME_LIMITS))

    async def get_post(self, _post_id):
        return SimpleNamespace(id=POST_ID, user_id=USER_ONE)

    async def store_upload(file, user_id, media_id):
        return StoredMedia("image/jpeg", file.size or 3, f"uploads/{user_id}/{media_id}.jpg")

    storage_upload = AsyncMock(side_effect=store_upload)
    create_record = AsyncMock(return_value=None)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr(media_router.media_storage, "upload", storage_upload)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", create_record)

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(media_router.router)
    db = AsyncMock()

    async def get_db():
        return db

    app.dependency_overrides[get_current_active_user] = current_user
    app.dependency_overrides[get_db_session] = get_db
    return SimpleNamespace(
        app=app,
        storage_upload=storage_upload,
        create_record=create_record,
        users=users,
    )


@pytest_asyncio.fixture
async def upload_client(upload_harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=upload_harness.app,
            client=("192.0.2.10", 1234),
        ),
        base_url="http://test",
    ) as client:
        yield client


async def send_upload(client, token="user-one", content=b"\xff\xd8\xff"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.post(
        f"/media/posts/{POST_ID}",
        headers=headers,
        files={"file": ("image.jpg", content, "image/jpeg")},
    )


def assert_limited(response, max_retry_after=60 * 60):
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "TOO_MANY_REQUESTS",
            "message": RATE_LIMIT_MESSAGE,
        }
    }
    assert 1 <= int(response.headers["Retry-After"]) <= max_retry_after


@pytest.mark.asyncio
async def test_upload_within_request_limit_succeeds(upload_client):
    for _ in range(10):
        response = await send_upload(upload_client)
        assert response.status_code == 201
        assert response.json()["post_id"] == str(POST_ID)


@pytest.mark.asyncio
async def test_repeated_uploads_eventually_return_429(upload_client, upload_harness):
    for _ in range(10):
        assert (await send_upload(upload_client)).status_code == 201

    assert_limited(await send_upload(upload_client))
    assert upload_harness.storage_upload.await_count == 10


@pytest.mark.asyncio
async def test_authenticated_users_have_separate_upload_request_buckets(upload_client):
    for _ in range(10):
        assert (await send_upload(upload_client, "user-one")).status_code == 201

    assert_limited(await send_upload(upload_client, "user-one"))
    assert (await send_upload(upload_client, "user-two")).status_code == 201


@pytest.mark.asyncio
async def test_anonymous_upload_attempts_use_separate_ip_buckets(upload_harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=upload_harness.app,
            client=("192.0.2.10", 1234),
        ),
        base_url="http://test",
    ) as first_ip:
        for _ in range(10):
            assert (await send_upload(first_ip, token="")).status_code == 401
        assert_limited(await send_upload(first_ip, token=""))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=upload_harness.app,
            client=("192.0.2.11", 1234),
        ),
        base_url="http://test",
    ) as second_ip:
        assert (await send_upload(second_ip, token="")).status_code == 401


@pytest.mark.asyncio
async def test_upload_volume_limit_rejects_before_storage(upload_client, upload_harness, monkeypatch):
    monkeypatch.setattr(
        media_router,
        "upload_volume_limiter",
        ActionRateLimiter({"media_upload_volume_bytes": ActionLimit(6, window_seconds=3600)}),
    )
    for _ in range(2):
        assert (await send_upload(upload_client, content=b"\xff\xd8\xff")).status_code == 201

    assert_limited(await send_upload(upload_client, content=b"\xff\xd8\xff"), 3600)
    assert upload_harness.storage_upload.await_count == 2


@pytest.mark.asyncio
async def test_normal_upload_response_is_unchanged(upload_client):
    response = await send_upload(upload_client)

    assert response.status_code == 201
    assert set(response.json()) == {"id", "post_id", "media_type", "url", "file_size_bytes"}
    assert response.json()["media_type"] == "image/jpeg"
    assert response.json()["file_size_bytes"] == 3


@pytest.mark.asyncio
async def test_global_60_per_ip_limiter_still_applies(upload_client, upload_harness):
    @upload_harness.app.get("/rate-limit-probe")
    async def rate_limit_probe():
        return {"ok": True}

    for _ in range(60):
        response = await upload_client.get("/rate-limit-probe")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "60"

    assert_limited(await upload_client.get("/rate-limit-probe"))
