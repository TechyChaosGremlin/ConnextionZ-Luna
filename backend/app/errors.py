"""
Global exception handlers for the ConnextionZ Platform API.

All errors are returned in the standardized format:
    { "error": { "code": str, "message": str, "details": dict | None } }

Per architecture standards (docs/LEAD_ARCHITECT_TASKS.md §Architecture Standards).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger()


class AppError(Exception):
    """Base application error with structured code and details."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        http_status: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        self.http_status = http_status
        super().__init__(message)


class NotFoundError(AppError):
    """Resource not found (HTTP 404)."""

    def __init__(self, message: str = "Resource not found", details: dict[str, Any] | None = None) -> None:
        super().__init__(code="NOT_FOUND", message=message, details=details, http_status=status.HTTP_404_NOT_FOUND)


class ConflictError(AppError):
    """Resource conflict, e.g. duplicate (HTTP 409)."""

    def __init__(self, message: str = "Resource already exists", details: dict[str, Any] | None = None) -> None:
        super().__init__(code="CONFLICT", message=message, details=details, http_status=status.HTTP_409_CONFLICT)


class ForbiddenError(AppError):
    """Access denied (HTTP 403)."""

    def __init__(self, message: str = "Access denied", details: dict[str, Any] | None = None) -> None:
        super().__init__(code="FORBIDDEN", message=message, details=details, http_status=status.HTTP_403_FORBIDDEN)


class UnauthorizedError(AppError):
    """Authentication required (HTTP 401)."""

    def __init__(self, message: str = "Authentication required", details: dict[str, Any] | None = None) -> None:
        super().__init__(code="UNAUTHORIZED", message=message, details=details, http_status=status.HTTP_401_UNAUTHORIZED)


class ValidationError(AppError):
    """Input validation failed (HTTP 422)."""

    def __init__(self, message: str = "Validation failed", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            details=details,
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


class InternalError(AppError):
    """Internal server error — do not expose details to client (HTTP 500)."""

    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(
            code="INTERNAL_ERROR",
            message=message,
            details=None,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _build_error_response(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    http_status: int = status.HTTP_400_BAD_REQUEST,
) -> JSONResponse:
    """Build a standardized error JSONResponse."""
    content: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if details is not None:
        content["error"]["details"] = details
    return JSONResponse(status_code=http_status, content=content)


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register all global exception handlers on the FastAPI application.

    Must be called during app factory creation.
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "application_error",
            code=exc.code,
            message=exc.message,
            status=exc.http_status,
            path=request.url.path,
        )
        return _build_error_response(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            http_status=exc.http_status,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Map HTTP status to a code
        status_code_map: dict[int, str] = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "TOO_MANY_REQUESTS",
            500: "INTERNAL_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }
        code = status_code_map.get(exc.status_code, "HTTP_ERROR")
        logger.warning(
            "http_exception",
            code=code,
            detail=str(exc.detail),
            status=exc.status_code,
            path=request.url.path,
        )
        response = _build_error_response(
            code=code,
            message=str(exc.detail),
            http_status=exc.status_code,
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Extract field-level details from pydantic errors
        field_errors: list[dict[str, Any]] = []
        for error in exc.errors():
            field_errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            })

        logger.warning(
            "validation_error",
            errors=field_errors,
            path=request.url.path,
        )
        return _build_error_response(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            details={"fields": field_errors},
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error",
            error_type=type(exc).__name__,
            error=str(exc),
            path=request.url.path,
        )
        return _build_error_response(
            code="INTERNAL_ERROR",
            message="An unexpected error occurred",
            details=None,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
