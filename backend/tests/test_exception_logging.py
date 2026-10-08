"""Exception logging checks using fabricated secrets and mocked database access."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

import app.db.session as database_session
import app.errors as errors

SENSITIVE = (
    "postgresql://audit-user:audit-password@db.invalid/audit-db "
    "Authorization: Bearer audit-token password=audit-password "
    "request-body=audit-private-data"
)


@pytest.fixture
def logging_capture(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    logger_name = "exception_logging_regression"
    logger = structlog.wrap_logger(
        logging.getLogger(logger_name),
        processors=[
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    monkeypatch.setattr(errors, "logger", logger)
    monkeypatch.setattr(database_session, "logger", logger)
    caplog.set_level(logging.WARNING, logger=logger_name)
    return caplog


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "status_code", "event"),
    [
        ("application", 400, "application_error"),
        ("http", 503, "http_exception"),
        ("validation", 422, "validation_error"),
        ("unhandled", 500, "unhandled_error"),
    ],
)
async def test_error_handler_logs_exclude_sensitive_content(
    logging_capture: pytest.LogCaptureFixture,
    kind: str,
    status_code: int,
    event: str,
) -> None:
    app = FastAPI()
    errors.register_exception_handlers(app)

    @app.get("/logging/{private_path}")
    async def fail(private_path: str) -> None:
        if kind == "application":
            raise errors.AppError(code=SENSITIVE, message=SENSITIVE)
        if kind == "http":
            raise HTTPException(status_code=503, detail=SENSITIVE)
        if kind == "validation":
            raise RequestValidationError(
                [{"loc": ("body", SENSITIVE), "msg": SENSITIVE, "type": "value_error"}],
                body=SENSITIVE,
            )
        try:
            raise ValueError(SENSITIVE)
        except ValueError as cause:
            raise RuntimeError(SENSITIVE) from cause

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/logging/audit-private-path",
            params={"token": "audit-query-token"},
            headers={"Authorization": "Bearer audit-header-token"},
        )

    assert response.status_code == status_code
    if kind == "application":
        assert response.json() == {"error": {"code": SENSITIVE, "message": SENSITIVE}}
    elif kind == "http":
        assert response.json() == {
            "error": {"code": "SERVICE_UNAVAILABLE", "message": SENSITIVE}
        }
    elif kind == "validation":
        assert response.json() == {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {
                    "fields": [
                        {"field": f"body.{SENSITIVE}", "message": SENSITIVE, "type": "value_error"}
                    ]
                },
            }
        }
    else:
        assert response.json() == {
            "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}
        }
    records = [
        record for record in logging_capture.records
        if record.name == "exception_logging_regression"
    ]
    assert len(records) == 1
    assert event in records[0].getMessage()
    assert "error_type" in records[0].getMessage()
    assert all(record.exc_info is None and record.stack_info is None for record in records)
    assert "Traceback" not in logging_capture.text
    assert "exception" not in records[0].getMessage().replace(event, "")
    assert "audit-" not in logging_capture.text
    assert "postgresql://" not in logging_capture.text
    assert "Authorization" not in logging_capture.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["connect", "execute"])
async def test_database_failure_logs_exclude_sensitive_content(
    monkeypatch: pytest.MonkeyPatch,
    logging_capture: pytest.LogCaptureFixture,
    failure_stage: str,
) -> None:
    connection = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = connection
    if failure_stage == "connect":
        context.__aenter__.side_effect = RuntimeError(SENSITIVE)
    else:
        connection.execute.side_effect = RuntimeError(SENSITIVE)
    engine = SimpleNamespace(connect=Mock(return_value=context))
    monkeypatch.setattr(database_session, "async_engine", engine)

    assert await database_session.check_db_connection() is False

    records = [
        record for record in logging_capture.records
        if record.name == "exception_logging_regression"
    ]
    assert len(records) == 1
    assert "Database connection failed" in records[0].getMessage()
    assert "RuntimeError" in records[0].getMessage()
    assert records[0].exc_info is None
    assert records[0].stack_info is None
    assert "audit-" not in logging_capture.text
    assert "Traceback" not in logging_capture.text
    engine.connect.assert_called_once()
    if failure_stage == "execute":
        connection.execute.assert_awaited_once()
        context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_success_remains_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    logging_capture: pytest.LogCaptureFixture,
) -> None:
    connection = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = connection
    engine = SimpleNamespace(connect=Mock(return_value=context))
    monkeypatch.setattr(database_session, "async_engine", engine)

    assert await database_session.check_db_connection() is True

    connection.execute.assert_awaited_once()
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    context.__aexit__.assert_awaited_once()
    assert not logging_capture.records
