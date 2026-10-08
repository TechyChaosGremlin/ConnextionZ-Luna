"""Focused health and error-envelope checks without service startup or live dependencies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.errors import ForbiddenError
from app.main import create_app


@pytest.fixture
def health_dependencies(monkeypatch):
    database = AsyncMock(return_value=True)
    redis = SimpleNamespace(
        connect=AsyncMock(),
        ping=AsyncMock(return_value=True),
        disconnect=AsyncMock(),
    )
    rabbitmq = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr("app.main.check_db_connection", database)
    monkeypatch.setattr("app.main.RedisService", lambda: redis)
    monkeypatch.setattr("app.main.RabbitMQService", lambda: rabbitmq)
    return database, redis, rabbitmq


@pytest.mark.asyncio
async def test_liveness_does_not_require_dependencies(health_dependencies):
    database, redis, rabbitmq = health_dependencies
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        health = await client.get("/health", headers={"X-Request-ID": "alpha-health"})
        live = await client.get("/health/live")

    assert health.status_code == live.status_code == 200
    assert health.json() == {"status": "healthy", "service": "connextionz-api"}
    assert live.json() == {"status": "alive"}
    assert health.headers["X-Request-ID"] == "alpha-health"
    database.assert_not_awaited()
    redis.connect.assert_not_awaited()
    rabbitmq.connect.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [None, "database_false", "database_error", "redis_false", "redis_error", "rabbitmq_error"],
)
@pytest.mark.asyncio
async def test_readiness_reports_each_dependency_failure(health_dependencies, failure):
    database, redis, rabbitmq = health_dependencies
    if failure == "database_false":
        database.return_value = False
    elif failure == "database_error":
        database.side_effect = RuntimeError("database unavailable")
    elif failure == "redis_false":
        redis.ping.return_value = False
    elif failure == "redis_error":
        redis.connect.side_effect = RuntimeError("redis unavailable")
    elif failure == "rabbitmq_error":
        rabbitmq.connect.side_effect = RuntimeError("rabbitmq unavailable")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    expected_checks = {"database": "ok", "redis": "ok", "rabbitmq": "ok"}
    if failure is not None:
        expected_checks[failure.split("_")[0]] = "error"
    assert response.json() == {
        "status": "ready" if failure is None else "not_ready",
        "checks": expected_checks,
    }
    database.assert_awaited_once()
    redis.connect.assert_awaited_once()
    rabbitmq.connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_application_and_validation_errors_keep_envelopes_and_request_ids():
    app = create_app()

    @app.get("/alpha-forbidden")
    async def forbidden():
        raise ForbiddenError("Owner access required")

    @app.get("/alpha-validation")
    async def validation(count: int):
        return {"count": count}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        forbidden_response = await client.get(
            "/alpha-forbidden", headers={"X-Request-ID": "alpha-forbidden"}
        )
        validation_response = await client.get("/alpha-validation?count=invalid")
        missing_response = await client.get("/alpha-missing")

    assert forbidden_response.status_code == 403
    assert forbidden_response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Owner access required"}
    }
    assert forbidden_response.headers["X-Request-ID"] == "alpha-forbidden"
    assert validation_response.status_code == 422
    error = validation_response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"][0]["field"] == "query.count"
    assert validation_response.headers["X-Request-ID"]
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_unhandled_exception_returns_sanitized_error():
    app = create_app()

    @app.get("/alpha-unexpected")
    async def unexpected():
        raise RuntimeError("Internal diagnostic must not reach the response")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/alpha-unexpected")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
        }
    }
