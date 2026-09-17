"""
ConnextionZ Platform — Application Configuration.

Uses pydantic-settings to load from environment / .env file.
All secrets and connection strings are sourced from environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────
    debug: bool = Field(default=False)
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )

    # ── JWT Authentication ───────────────────────────────────────
    jwt_secret_key: SecretStr = Field(
        default=SecretStr("change-me-in-production-32-bytes-minimum"),
        description="Secret key for JWT token signing",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm")
    jwt_access_token_expire_minutes: int = Field(
        default=15, description="Access token expiration in minutes"
    )
    jwt_refresh_token_expire_days: int = Field(
        default=7, description="Refresh token expiration in days"
    )

    # ── Session & Cache ─────────────────────────────────────────
    session_ttl_seconds: int = Field(
        default=86400, description="Session TTL in seconds (24 hours)"
    )
    cache_ttl_seconds: int = Field(
        default=300, description="Default cache TTL in seconds (5 minutes)"
    )
    redis_max_connections: int = Field(
        default=10, description="Maximum Redis connection pool size"
    )

    # ── Database ─────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/connextionz",
        description="Async PostgreSQL connection string (asyncpg driver)",
    )
    database_url_sync: str = Field(
        default="",
        description="Sync PostgreSQL connection string for Alembic (psycopg). "
        "Auto-derived from database_url if empty.",
    )
    postgres_user: str = Field(default="postgres")
    postgres_password: SecretStr = Field(default=SecretStr("password"))
    postgres_db: str = Field(default="connextionz")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)

    # ── Redis ────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_password: SecretStr = Field(default=SecretStr(""))

    # ── RabbitMQ ─────────────────────────────────────────────────
    rabbitmq_url: str = Field(default="amqp://guest:guest@localhost:5672/")
    rabbitmq_host: str = Field(default="localhost")
    rabbitmq_port: int = Field(default=5672)
    rabbitmq_user: str = Field(default="guest")
    rabbitmq_password: SecretStr = Field(default=SecretStr("guest"))

    # ── LLM / AI ─────────────────────────────────────────────────
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))

    # ── AWS ──────────────────────────────────────────────────────
    aws_access_key_id: str = Field(default="test")
    aws_secret_access_key: SecretStr = Field(default=SecretStr("test"))
    aws_region: str = Field(default="us-east-1")
    aws_endpoint_url: str = Field(default="")
    aws_s3_bucket: str = Field(default="connextionz-media")

    # ── CORS ─────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:8000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    # ── GraphQL ──────────────────────────────────────────────────
    graphql_path: str = Field(default="/graphql")
    graphql_playground: bool = Field(default=True)

    # ── Logging ──────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    log_format: Literal["json", "text"] = Field(default="json")

    # ── Feature Flags ────────────────────────────────────────────
    enable_two_tower_model: bool = Field(default=True)
    enable_agentic_router: bool = Field(default=True)
    enable_realtime_notifications: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Ensure critical secrets and runtime flags are safe in production."""
        if self.environment == "production":
            if self.debug:
                raise ValueError("FATAL: DEBUG must be disabled in production.")
            if self.jwt_secret_key.get_secret_value() == "change-me-in-production-32-bytes-minimum":
                raise ValueError(
                    "FATAL: JWT_SECRET_KEY must be set to a secure value in production. "
                    "Do not use the default insecure value."
                )
            if len(self.jwt_secret_key.get_secret_value()) < 32:
                raise ValueError("FATAL: JWT_SECRET_KEY must be at least 32 characters long in production.")
        return self

    # ── Rate Limiting ────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=60)
    rate_limit_burst: int = Field(default=10)

    # ── Pagination ───────────────────────────────────────────────
    default_page_size: int = Field(default=20)
    max_page_size: int = Field(default=100)

    @property
    def sync_database_url(self) -> str:
        """Return a synchronous (psycopg) database URL for Alembic."""
        if self.database_url_sync:
            return self.database_url_sync
        # Derive from async URL: replace asyncpg with the installed sync psycopg driver.
        return (
            self.database_url.replace("+asyncpg", "+psycopg")
            .replace("postgresql://", "postgresql+psycopg://", 1)
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()


# Module-level settings instance for convenient imports
settings = get_settings()