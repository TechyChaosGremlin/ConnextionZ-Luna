"""
Database session management for SQLAlchemy async.

Provides:
- Async engine creation
- Session factory
- Database initialization
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.base import Base


def _engine_options_for_url(database_url: str) -> dict[str, Any]:
    """Configure SSL and pooling for Supabase connection modes."""
    url = make_url(database_url)
    host = (url.host or "").lower()
    is_supabase = host.endswith(".supabase.co") or host.endswith(
        ".pooler.supabase.com"
    )
    is_transaction_pooler = is_supabase and url.port == 6543

    connect_args: dict[str, Any] = {"timeout": 10}
    if is_supabase and "sslmode" not in url.query and "ssl" not in url.query:
        connect_args["ssl"] = "require"
    if is_transaction_pooler:
        connect_args["statement_cache_size"] = 0
        return {"connect_args": connect_args, "poolclass": NullPool}

    return {
        "connect_args": connect_args,
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }


# Create async engine
async_engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    **_engine_options_for_url(settings.database_url),
)

# Create async session factory
async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    """
    Initialize the database.

    Creates all tables based on models.
    Should only be used in development/testing.
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    """
    Dependency that provides a database session.

    Yields:
        AsyncSession: A database session
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """
    Check if the database connection is working.

    Returns:
        True if connection is successful, False otherwise

    Failures log only the exception type, never connection details or tracebacks.
    """
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.error(
            "Database connection failed",
            error_type=type(e).__name__,
            exc_info=False,
            stack_info=False,
        )
        return False


# Placeholder for logger
import structlog

logger = structlog.get_logger()
