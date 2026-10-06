"""
FastAPI application entry point and app factory.

Creates the FastAPI application with all routers, middleware, and configuration.
"""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import asynccontextmanager
import signal
import math
import time
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.config import settings
from app.errors import register_exception_handlers
from app.rate_limits import (
    AUTH_ENDPOINT_ACTIONS,
    AUTH_ENDPOINT_LIMITS,
    RATE_LIMIT_MESSAGE,
    UPLOAD_REQUEST_ACTION,
    UPLOAD_REQUEST_LIMITS,
    ActionRateLimitExceeded,
    ActionRateLimiter,
    client_identity,
)
from app.logging_config import configure_logging, RequestIDMiddleware
from app.db.session import async_session_factory
from app.db.session import check_db_connection
from api.graphql import create_graphql_router
from features.auth.router import router as auth_router
from features.auth.jwt import ACCESS_TOKEN_TYPE, JWTError, REFRESH_TOKEN_TYPE, decode_token
from features.media.router import router as media_router
from features.streaming.router import router as streaming_router
from features.streaming.service import stream_manager
from services.redis_service import RedisService
from services.rabbitmq_service import rabbitmq_service

logger = structlog.get_logger()
shutdown_event = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory per-IP rate limiter for production-safe API protection."""

    def __init__(self, app: ASGIApp, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = max(1, requests_per_minute)
        self._history: dict[str, deque[float]] = defaultdict(deque)
        self.auth_action_limiter = ActionRateLimiter(AUTH_ENDPOINT_LIMITS)
        self.upload_action_limiter = ActionRateLimiter(UPLOAD_REQUEST_LIMITS)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/health/live", "/health/ready"}:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = self._history[client_ip]

        while bucket and now - bucket[0] >= 60:
            bucket.popleft()

        if len(bucket) >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(max(1, math.ceil(bucket[0] + 60 - now)))},
                content={
                    "error": {
                        "code": "TOO_MANY_REQUESTS",
                        "message": RATE_LIMIT_MESSAGE,
                    }
                },
            )

        bucket.append(now)
        auth_action = AUTH_ENDPOINT_ACTIONS.get(request.url.path)
        if request.method == "POST" and auth_action is not None:
            try:
                self.auth_action_limiter.consume(
                    client_identity(None, client_ip),
                    {auth_action: 1},
                )
            except ActionRateLimitExceeded as exc:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(exc.retry_after)},
                    content={
                        "error": {
                            "code": "TOO_MANY_REQUESTS",
                            "message": RATE_LIMIT_MESSAGE,
                        }
                    },
                )

        if request.method == "POST" and _is_media_post_upload(request.url.path):
            try:
                self.upload_action_limiter.consume(
                    _upload_client_identity(request, client_ip),
                    {UPLOAD_REQUEST_ACTION: 1},
                )
            except ActionRateLimitExceeded as exc:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(exc.retry_after)},
                    content={
                        "error": {
                            "code": "TOO_MANY_REQUESTS",
                            "message": RATE_LIMIT_MESSAGE,
                        }
                    },
                )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.requests_per_minute - len(bucket)))
        return response


def _is_media_post_upload(path: str) -> bool:
    parts = path.strip("/").split("/")
    return len(parts) == 3 and parts[:2] == ["media", "posts"] and bool(parts[2])


def _upload_client_identity(request: Request, client_ip: str) -> tuple[str, str]:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        try:
            payload = decode_token(authorization[len("Bearer ") :])
        except JWTError:
            payload = {}
        if payload.get("type") in {ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE} and payload.get("sub"):
            return client_identity(str(payload["sub"]), client_ip)
    return client_identity(None, client_ip)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan events.

    Handles startup and shutdown events.
    """
    # Startup
    configure_logging()
    logger.info("Starting ConnextionZ Platform API", environment=settings.environment)

    redis_service = RedisService()
    try:
        await redis_service.connect()
        logger.info("Redis connected")
    except Exception as exc:
        logger.warning("Redis connection failed during startup", error=str(exc))
        if settings.environment == "production":
            raise

    try:
        await rabbitmq_service.connect()
        logger.info("RabbitMQ connected")
    except Exception as exc:
        logger.warning("RabbitMQ connection failed during startup", error=str(exc))
        if settings.environment == "production":
            raise

    yield

    # Shutdown
    logger.info("Shutting down ConnextionZ Platform API")
    await stream_manager.cleanup()
    try:
        await redis_service.disconnect()
    except Exception:
        pass
    try:
        await rabbitmq_service.disconnect()
    except Exception:
        pass


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    # Disable docs in production for security
    docs_url = "/api/docs" if settings.debug else None
    redoc_url = "/api/redoc" if settings.debug else None
    openapi_url = "/api/openapi.json" if settings.debug else None
    
    app = FastAPI(
        title="ConnextionZ Platform API",
        description="API for the ConnextionZ creator collaboration platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    # ── Middleware ──────────────────────────────────────────

    # Request ID propagation (must be added early)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if settings.environment == "production":
            response.headers.setdefault("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none';")
        return response

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception Handlers ──────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ─────────────────────────────────────────────
    app.include_router(auth_router)
    app.include_router(media_router)
    app.include_router(streaming_router)

    # GraphQL (Strawberry) — served at /graphql to match the frontend's api-config.ts
    graphql_router = create_graphql_router(async_session_factory)
    app.include_router(graphql_router, prefix="/graphql")

    # ── Health Checks ───────────────────────────────────────

    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        """Basic health check endpoint."""
        return {"status": "healthy", "service": "connextionz-api"}

    @app.get("/health/ready", tags=["health"])
    async def readiness_check() -> dict:
        """Readiness check — verifies dependencies are available."""
        checks = {}

        try:
            db_ok = await check_db_connection()
            checks["database"] = "ok" if db_ok else "error"
        except Exception as e:
            logger.error("Health check failed", check="database", error=str(e))
            checks["database"] = "error"

        try:
            redis_service = RedisService()
            await redis_service.connect()
            redis_ok = await redis_service.ping()
            await redis_service.disconnect()
            checks["redis"] = "ok" if redis_ok else "error"
        except Exception as e:
            logger.error("Health check failed", check="redis", error=str(e))
            checks["redis"] = "error"

        try:
            await rabbitmq_service.connect()
            await rabbitmq_service.disconnect()
            checks["rabbitmq"] = "ok"
        except Exception as e:
            logger.error("Health check failed", check="rabbitmq", error=str(e))
            checks["rabbitmq"] = "error"

        ready = all(v == "ok" for v in checks.values())
        status = "ready" if ready else "not_ready"

        return {"status": status, "checks": checks}

    @app.get("/health/live", tags=["health"])
    async def liveness_check() -> dict:
        """Liveness check — verifies the application is running."""
        return {"status": "alive"}

    return app


# Create the application instance
app = create_app()
