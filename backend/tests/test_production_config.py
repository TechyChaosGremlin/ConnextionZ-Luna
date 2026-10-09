import pytest
import httpx

from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.session import _engine_options_for_url
from app.main import create_app


class _TestSettings(Settings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )


def _production_values(**overrides):
    values = {
        "environment": "production",
        "debug": False,
        "jwt_secret_key": "a-very-long-production-secret-that-is-safe-123",
        "database_url": "postgresql+asyncpg://beta_user:db-secret-unique@db.example.test:5432/connextionz",
        "redis_url": "rediss://:redis-secret-unique@redis.example.test:6379/0",
        "rabbitmq_url": "amqps://beta_user:broker-secret-unique@broker.example.test:5671/",
    }
    values.update(overrides)
    return values


def _production_settings(**overrides):
    return _TestSettings(**_production_values(**overrides))


@pytest.mark.parametrize(("value", "expected"), [(False, False), (True, True), (0, False), (1, True)])
def test_debug_preserves_boolean_and_integer_inputs(value, expected):
    assert _TestSettings(environment="development", debug=value).debug is expected


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", " No ", "n", "f"])
def test_production_accepts_false_debug_environment_values(monkeypatch, value):
    monkeypatch.setenv("DEBUG", value)
    values = _production_values()
    values.pop("debug")

    assert _TestSettings(**values).debug is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " Yes ", "y", "t"])
def test_nonproduction_accepts_true_debug_environment_values(monkeypatch, value):
    monkeypatch.setenv("DEBUG", value)

    assert _TestSettings(environment="development").debug is True


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " Yes ", "y", "t"])
def test_production_rejects_true_debug_environment_values(monkeypatch, value):
    monkeypatch.setenv("DEBUG", value)
    values = _production_values()
    values.pop("debug")

    with pytest.raises(ValidationError, match="DEBUG must be disabled in production"):
        _TestSettings(**values)


@pytest.mark.parametrize("value", ["", "2", "enabled", "invalid-debug-sensitive-marker"])
@pytest.mark.parametrize("environment", ["development", "production"])
def test_invalid_debug_environment_values_have_clear_errors(monkeypatch, value, environment):
    monkeypatch.setenv("DEBUG", value)
    values = _production_values(environment=environment)
    values.pop("debug")

    with pytest.raises(ValidationError, match="DEBUG must be a boolean") as error:
        _TestSettings(**values)

    details = error.value.errors(include_input=False)
    assert details[0]["loc"] == ("debug",)
    assert details[0]["type"] == "value_error"
    assert "invalid-debug-sensitive-marker" not in str(error.value)


def test_production_rejects_debug_mode():
    with pytest.raises(ValueError, match="DEBUG.*production"):
        _TestSettings(
            environment="production",
            debug=True,
            jwt_secret_key=SecretStr("a-very-long-production-secret-that-is-safe-123"),
        )


def test_production_requires_secure_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY.*secure"):
        _TestSettings(
            environment="production",
            debug=False,
            jwt_secret_key=SecretStr("change-me-in-production-32-bytes-minimum"),
        )


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY.*32"):
        _TestSettings(
            environment="production",
            debug=False,
            jwt_secret_key=SecretStr("short-test-secret"),
        )


@pytest.mark.parametrize(
    ("field_name", "environment_name"),
    [
        ("database_url", "DATABASE_URL"),
        ("redis_url", "REDIS_URL"),
        ("rabbitmq_url", "RABBITMQ_URL"),
    ],
)
def test_production_requires_explicit_dependency_urls(field_name, environment_name):
    values = _production_values()
    values.pop(field_name)
    with pytest.raises(ValueError, match=environment_name):
        _TestSettings(**values)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        (
            "database_url",
            "postgresql+asyncpg://postgres:password@localhost:5432/connextionz",
            "DATABASE_URL.*localhost",
        ),
        (
            "database_url",
            "postgresql+asyncpg://postgres:password@db.example.test:5432/connextionz",
            "DATABASE_URL.*default or insecure",
        ),
        ("database_url_sync", "postgresql+psycopg://postgres:postgres@db.example.test:5432/connextionz",
         "DATABASE_URL_SYNC.*default or insecure"),
        ("redis_url", "redis://localhost:6379/0", "REDIS_URL.*localhost"),
        (
            "redis_url",
            "redis://:password@redis.example.test:6379/0",
            "REDIS_URL.*default or insecure",
        ),
        (
            "rabbitmq_url",
            "amqp://guest:guest@broker.example.test:5672/",
            "RABBITMQ_URL.*default or insecure",
        ),
    ],
)
def test_production_rejects_local_or_default_dependency_configuration(
    field_name, value, message
):
    with pytest.raises(ValueError, match=message):
        _production_settings(**{field_name: value})


def test_production_accepts_explicit_secure_dependency_urls():
    configured = _production_settings(
        database_url_sync=(
            "postgresql+psycopg://migration_user:migration-secret-unique"
            "@db.example.test:5432/connextionz"
        )
    )

    assert configured.database_url.startswith("postgresql+asyncpg://")
    assert configured.database_url_sync.startswith("postgresql+psycopg://")
    assert configured.redis_url.startswith("rediss://")
    assert configured.rabbitmq_url.startswith("amqps://")

    derived_sync_url = _production_settings()
    assert derived_sync_url.database_url_sync == ""
    assert derived_sync_url.sync_database_url.startswith("postgresql+psycopg://")


def test_supabase_transaction_pooler_disables_statement_cache_and_app_pool():
    options = _engine_options_for_url(
        "postgresql+asyncpg://db-user:db-password"
        "@aws-0-region.pooler.supabase.com:6543/postgres"
    )

    assert options["poolclass"] is NullPool
    assert options["connect_args"] == {
        "timeout": 10,
        "ssl": "require",
        "statement_cache_size": 0,
    }


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://db-user:db-password"
        "@db.project-ref.supabase.co:5432/postgres",
        "postgresql+asyncpg://db-user:db-password"
        "@aws-0-region.pooler.supabase.com:5432/postgres",
    ],
)
def test_supabase_direct_and_session_connections_require_ssl_and_keep_pooling(
    database_url,
):
    options = _engine_options_for_url(database_url)

    assert options["connect_args"] == {"timeout": 10, "ssl": "require"}
    assert options["pool_pre_ping"] is True
    assert options["pool_size"] == 10
    assert options["max_overflow"] == 20
    assert "poolclass" not in options


def test_supabase_explicit_ssl_settings_are_preserved():
    options = _engine_options_for_url(
        "postgresql+asyncpg://db-user:db-password"
        "@db.project-ref.supabase.co:5432/postgres?sslmode=verify-full"
    )

    assert options["connect_args"] == {"timeout": 10}


def test_non_supabase_database_on_port_6543_keeps_existing_pooling():
    options = _engine_options_for_url(
        "postgresql+asyncpg://db-user:db-password@db.example.test:6543/app"
    )

    assert options["connect_args"] == {"timeout": 10}
    assert options["pool_pre_ping"] is True
    assert options["pool_size"] == 10
    assert options["max_overflow"] == 20


def test_production_configuration_errors_do_not_echo_connection_secrets():
    secret = "unique-sensitive-test-secret"

    with pytest.raises(ValueError) as error:
        _production_settings(
            redis_url=f"redis://:{secret}@localhost:6379/0"
        )

    assert "REDIS_URL" in str(error.value)
    assert secret not in str(error.value)


@pytest.mark.parametrize("environment", ["development", "test", "local"])
def test_nonproduction_environments_keep_local_dependency_defaults(environment):
    configured = _TestSettings(environment=environment)

    assert configured.database_url.startswith("postgresql")
    assert configured.redis_url == "redis://localhost:6379/0"
    assert configured.rabbitmq_url.startswith("amqp://")
    assert configured.sync_database_url.startswith("postgresql+psycopg://")


@pytest.mark.asyncio
async def test_production_factory_disables_docs_and_sets_security_headers(monkeypatch):
    monkeypatch.setattr("app.main.settings.environment", "production")
    monkeypatch.setattr("app.main.settings.debug", False)
    app = create_app()

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/health", headers={"X-Request-ID": "production-check"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "production-check"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'; frame-ancestors 'none';"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_configured_cors_only_allows_approved_origin(monkeypatch, allowed):
    approved_origin = "https://alpha.example.test"
    monkeypatch.setattr("app.main.settings.cors_origins", [approved_origin])
    origin = approved_origin if allowed else "https://unapproved.example.test"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
    ) as client:
        response = await client.options(
            "/graphql",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == (200 if allowed else 400)
    if allowed:
        assert response.headers["Access-Control-Allow-Origin"] == approved_origin
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
    else:
        assert "Access-Control-Allow-Origin" not in response.headers
