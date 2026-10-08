"""
Alembic environment configuration.

Uses the synchronous database URL (psycopg2) for migration operations.
Imports all SQLAlchemy models so ``Base.metadata`` is fully populated
for ``autogenerate`` support.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Import all models so Base.metadata is populated ──────────────
# isort: off
from app.models import Base  # noqa: E402

# Import model modules to register table metadata
import app.models.user  # noqa: E402, F401
import app.models.content  # noqa: E402, F401
import app.models.collaboration  # noqa: E402, F401
import app.models.collaboration_payment  # noqa: E402, F401
import app.models.reputation  # noqa: E402, F401
import app.models.embedding  # noqa: E402, F401
import app.models.notification  # noqa: E402, F401
import app.models.messaging  # noqa: E402, F401
import app.models.social  # noqa: E402, F401

# ── Database URL ─────────────────────────────────────────────────
# Prefer DATABASE_URL from environment; fall back to alembic.ini [alembic] sqlalchemy.url
from app.config import get_settings

settings = get_settings()
sync_url = settings.sync_database_url

if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))

# ── Metadata target ──────────────────────────────────────────────
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Calls to ``context.execute()`` emit the given SQL string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()