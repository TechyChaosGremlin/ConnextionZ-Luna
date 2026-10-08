"""
ConnextionZ Platform — Application Configuration.

Uses pydantic-settings to load from environment / .env file.
All secrets and connection strings are sourced from environment variables.
DEBUG accepts case-insensitive boolean spellings with surrounding whitespace.
Invalid DEBUG values are rejected; production still requires DEBUG=false.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, TypeAdapter, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_INSECURE_DEFAULT_PASSWORDS = {
    "password",
    "guest",
    "default",
    "changeme",
    "change-me",
    "123456",
    "postgres",
    "redis",
    "rabbitmq",
}
_LOCAL_HOSTS = {"localhost", "localhost.localdomain"}
_DEBUG_BOOLEAN_ADAPTER = TypeAdapter(bool)


def _is_local_host(hostname: str) -> bool:
    if hostname in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_production_url(
    value: str,
    setting_name: str,
    allowed_schemes: set[str],
    *,
    require_username: bool = True,
) -> None:
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        username = unquote(parsed.username or "").lower()
        password = unquote(parsed.password or "").lower()
    except ValueError:
        raise ValueError(f"{setting_name} must be a valid production connection URL.") from None

    if parsed.scheme not in allowed_schemes or not hostname:
        raise ValueError(
            f"{setting_name} must use a supported scheme and specify a hostname."
        )
    if _is_local_host(hostname):
        raise ValueError(f"{setting_name} must not use a localhost endpoint in production.")
    if (require_username and not username) or not password:
        raise ValueError(
            f"{setting_name} must include explicit non-default credentials in production."
        )
    if (
        username == "guest"
        or password in _INSECURE_DEFAULT_PASSWORDS
        or username == password
    ):
        raise ValueError(
            f"{setting_name} must not use default or insecure credentials in production."
        )


class Settings(BaseSettings):
    """Top-level application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # ── Application ──────────────────────────────────────────────
    debug: bool = Field(default=False)
    environment: Literal["development", "test", "local", "staging", "production"] = Field(
        default="development"
    )

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: object) -> bool:
        """Validate DEBUG without including its supplied value in the error."""
        if isinstance(value, str):
            value = value.strip()
        try:
            return _DEBUG_BOOLEAN_ADAPTER.validate_python(value)
        except ValidationError:
            raise ValueError(
                "DEBUG must be a boolean: true/false, 1/0, yes/no, or on/off."
            ) from None

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

    # ── Media storage (AWS S3 or explicitly configured S3-compatible endpoint) ──
    aws_access_key_id: str | None = Field(default=None)
    aws_secret_access_key: SecretStr | None = Field(default=None)
    aws_region: str | None = Field(default=None)
    aws_endpoint_url: str = Field(default="")
    aws_s3_bucket: str | None = Field(default=None)
    media_max_image_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    media_max_video_bytes: int = Field(default=512 * 1024 * 1024, gt=0)

    # ── Streaming ────────────────────────────────────────────────
    ffmpeg_path: str = Field(default="ffmpeg")
    ffmpeg_startup_timeout_seconds: float = Field(default=1.0, gt=0)
    ffmpeg_stop_timeout_seconds: float = Field(default=10.0, gt=0)
    streaming_viewer_lease_seconds: int = Field(default=60, gt=0, le=3600)
    streaming_twitch_destination_url: SecretStr = Field(
        default=SecretStr("rtmp://127.0.0.1:1935/live/luna-twitch-test")
    )
    streaming_youtube_destination_url: SecretStr = Field(
        default=SecretStr("rtmp://127.0.0.1:1935/live/luna-youtube-test")
    )
    streaming_kick_destination_url: SecretStr = Field(
        default=SecretStr("rtmp://127.0.0.1:1935/live/luna-kick-test")
    )
    streaming_facebook_destination_url: SecretStr = Field(
        default=SecretStr("rtmp://127.0.0.1:1935/live/luna-facebook-test")
    )

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

    # ── Collaboration payments ───────────────────────────────────
    collaboration_payment_provider: Literal["disabled", "fake"] = Field(
        default="disabled"
    )
    collaboration_payment_real_money_enabled: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Ensure critical secrets and runtime flags are safe in production."""
        if self.collaboration_payment_real_money_enabled:
            raise ValueError(
                "Real-money collaboration payments are not supported by any configured provider"
            )
        if (
            self.environment == "production"
            and self.collaboration_payment_provider == "fake"
        ):
            raise ValueError(
                "The fake collaboration payment provider cannot be selected in production"
            )
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
            required_urls = {
                "database_url": "DATABASE_URL",
                "redis_url": "REDIS_URL",
                "rabbitmq_url": "RABBITMQ_URL",
            }
            missing_urls = [
                env_name
                for field_name, env_name in required_urls.items()
                if field_name not in self.model_fields_set
                or not getattr(self, field_name).strip()
            ]
            if missing_urls:
                raise ValueError(
                    "Production requires explicit configuration for: "
                    + ", ".join(missing_urls)
                    + "."
                )

            _validate_production_url(
                self.database_url,
                "DATABASE_URL",
                {"postgresql+asyncpg"},
            )
            if self.database_url_sync.strip():
                _validate_production_url(
                    self.database_url_sync,
                    "DATABASE_URL_SYNC",
                    {"postgresql+psycopg"},
                )
            _validate_production_url(
                self.redis_url,
                "REDIS_URL",
                {"redis", "rediss"},
                require_username=False,
            )
            _validate_production_url(
                self.rabbitmq_url,
                "RABBITMQ_URL",
                {"amqp", "amqps"},
            )
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