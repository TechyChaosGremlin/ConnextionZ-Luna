"""Focused beta startup/shutdown checks without shared-service changes."""

from pathlib import Path
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import httpx
import structlog
from fastapi import FastAPI

import app.main as main


@pytest.fixture
def lifecycle(monkeypatch):
    redis = SimpleNamespace(
        connect=AsyncMock(), ping=AsyncMock(return_value=True), disconnect=AsyncMock()
    )
    rabbitmq = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())
    cleanup = AsyncMock()
    monkeypatch.setattr(main, "configure_logging", Mock())
    monkeypatch.setattr(main, "RedisService", lambda: redis)
    monkeypatch.setattr(main, "rabbitmq_service", rabbitmq)
    monkeypatch.setattr(main.stream_manager, "cleanup", cleanup)
    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(main, "logger", Mock())
    return redis, rabbitmq, cleanup


@pytest.mark.asyncio
async def test_production_startup_requires_real_redis_ping(lifecycle):
    redis, rabbitmq, _cleanup = lifecycle
    redis.ping.return_value = False
    with pytest.raises(RuntimeError, match="Redis"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Production started without Redis")
    redis.disconnect.assert_awaited_once()
    rabbitmq.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_startup_failure_closes_acquired_resources(lifecycle):
    redis, rabbitmq, _cleanup = lifecycle
    rabbitmq.connect.side_effect = RuntimeError("broker unavailable")
    with pytest.raises(RuntimeError, match="broker unavailable"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Production started without RabbitMQ")
    redis.disconnect.assert_awaited_once()
    rabbitmq.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_body_failure_still_cleans_up(lifecycle):
    redis, rabbitmq, cleanup = lifecycle
    with pytest.raises(RuntimeError, match="application failure"):
        async with main.lifespan(FastAPI()):
            raise RuntimeError("application failure")
    cleanup.assert_awaited_once()
    redis.disconnect.assert_awaited_once()
    rabbitmq.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_cleanup_failure_does_not_skip_dependency_cleanup(lifecycle):
    redis, rabbitmq, cleanup = lifecycle
    cleanup.side_effect = RuntimeError("stream cleanup failure")
    async with main.lifespan(FastAPI()):
        pass
    redis.disconnect.assert_awaited_once()
    rabbitmq.disconnect.assert_awaited_once()
    main.logger.error.assert_called()


@pytest.mark.asyncio
async def test_normal_startup_and_shutdown(lifecycle):
    redis, rabbitmq, cleanup = lifecycle
    async with main.lifespan(FastAPI()):
        redis.ping.assert_awaited_once()
        rabbitmq.connect.assert_awaited_once()
        cleanup.assert_not_awaited()
    cleanup.assert_awaited_once()
    redis.disconnect.assert_awaited_once()
    rabbitmq.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_does_not_disconnect_running_application_broker(lifecycle, monkeypatch):
    _redis, rabbitmq, _cleanup = lifecycle
    probe = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr(main, "RabbitMQService", lambda: probe, raising=False)
    monkeypatch.setattr(main, "check_db_connection", AsyncMock(return_value=True))
    app = main.create_app()
    async with main.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")
        assert response.json()["status"] == "ready"
        rabbitmq.disconnect.assert_not_awaited()
        probe.connect.assert_awaited_once()
        probe.disconnect.assert_awaited_once()
    rabbitmq.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_development_keeps_existing_dependency_failure_tolerance(lifecycle, monkeypatch):
    redis, rabbitmq, cleanup = lifecycle
    monkeypatch.setattr(main.settings, "environment", "development")
    redis.ping.return_value = False
    rabbitmq.connect.side_effect = RuntimeError("broker unavailable")
    async with main.lifespan(FastAPI()):
        pass
    cleanup.assert_awaited_once()
    redis.disconnect.assert_awaited_once()
    rabbitmq.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_error_is_reported_without_exception_contents(lifecycle):
    redis, rabbitmq, _cleanup = lifecycle
    redis.disconnect.side_effect = RuntimeError("dummy-sensitive-diagnostic")
    async with main.lifespan(FastAPI()):
        pass
    rabbitmq.disconnect.assert_awaited_once()
    assert main.logger.error.call_args.kwargs == {
        "dependency": "redis", "error_type": "RuntimeError",
    }
    assert "dummy-sensitive-diagnostic" not in str(main.logger.error.call_args)


def test_production_logging_is_json_and_suppresses_access_queries(monkeypatch, capsys):
    from app.logging_config import configure_logging

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    monkeypatch.setattr(root, "level", root.level)
    for name in ("uvicorn.access", "sqlalchemy.engine", "botocore"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "level", logger.level)
    monkeypatch.setattr(main.settings, "debug", False)
    monkeypatch.setattr(main.settings, "log_level", "INFO")
    monkeypatch.setattr(main.settings, "log_format", "json")
    original = structlog.get_config()
    try:
        configure_logging()
        logging.getLogger("beta.probe").info("deployment check")
        event = json.loads(capsys.readouterr().err)
        assert event["event"] == "deployment check"
        assert event["level"] == "info"
        assert event["timestamp"]
        assert not logging.getLogger("uvicorn.access").isEnabledFor(logging.INFO)
    finally:
        structlog.configure(**original)


def test_beta_image_uses_canonical_backend_package_and_migrations():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    assert "WORKDIR /app/backend" in dockerfile
    assert "ENV ENVIRONMENT=production" in dockerfile
    assert "ENV DEBUG=false" in dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile
    assert "COPY migrations migrations" not in dockerfile
    assert (root / "backend" / "app" / "main.py").is_file()
    assert (root / "backend" / "alembic.ini").is_file()
    assert (root / "backend" / "alembic" / "env.py").is_file()


def test_beta_image_context_excludes_local_secrets():
    root = Path(__file__).resolve().parents[2]
    patterns = (root / ".dockerignore").read_text().splitlines()
    assert "**/.env" in patterns
    assert "**/.env.*" in patterns
    assert ".venv" in patterns


def test_root_compose_uses_canonical_backend_dependency_configuration():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text()
    api = compose.split("  api:\n", 1)[1].split("\n  postgres:\n", 1)[0]

    assert "context: ." in api
    assert "dockerfile: Dockerfile" in api
    assert "ENVIRONMENT: production" in api
    assert 'DEBUG: "false"' in api
    assert "JWT_SECRET_KEY: ${JWT_SECRET_KEY:?set JWT_SECRET_KEY}" in api
    assert "SESSION_SECRET" not in api
    assert "postgresql+asyncpg://" in api
    assert "postgresql+psycopg://" in api
    assert "@postgres:5432/" in api
    assert "REDIS_URL: redis://:${REDIS_PASSWORD:?set REDIS_PASSWORD}@redis:" in api
    assert "RABBITMQ_URL: amqp://${RABBITMQ_USER:?set RABBITMQ_USER}:${RABBITMQ_PASSWORD:?set RABBITMQ_PASSWORD}@rabbitmq:" in api
    assert "localhost" not in api
    assert "condition: service_healthy" in api
    assert "/health/live" in api

    for required in (
        "JWT_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "RABBITMQ_USER",
        "RABBITMQ_PASSWORD",
        "CORS_ORIGINS",
    ):
        assert f"${{{required}:?set {required}}}" in api


def test_root_compose_environment_template_has_no_credential_values():
    root = Path(__file__).resolve().parents[2]
    template = (root / ".env.example").read_text()
    assert "SESSION_SECRET" not in template
    assert "JWT_SECRET_KEY=\n" in template
    assert "POSTGRES_PASSWORD=\n" in template
    assert "REDIS_PASSWORD=\n" in template
    assert "RABBITMQ_PASSWORD=\n" in template
    assert "CORS_ORIGINS=\n" in template
    for variable in (
        "POSTGRES_USER",
        "POSTGRES_DB",
        "POSTGRES_PORT",
        "REDIS_DB",
        "REDIS_PORT",
        "RABBITMQ_USER",
        "RABBITMQ_PORT",
        "BACKEND_PORT",
        "LOG_LEVEL",
        "AWS_S3_BUCKET",
        "AWS_ENDPOINT_URL",
    ):
        assert f"{variable}=" in template
