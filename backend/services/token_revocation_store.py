"""Database-backed revocation shared across serverless instances."""

from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.session import async_session_factory
from app.models.token_revocation import TokenRevocation

logger = structlog.get_logger()


async def check_revocation_store() -> None:
    async with async_session_factory() as session:
        await session.execute(select(TokenRevocation.id).limit(0))


async def is_revoked(jti: str) -> bool:
    try:
        async with async_session_factory() as session:
            revoked = await session.scalar(
                select(TokenRevocation.id).where(
                    TokenRevocation.jti == jti,
                    TokenRevocation.expires_at > datetime.now(timezone.utc),
                )
            )
            return revoked is not None
    except Exception as exc:
        logger.error("Token revocation lookup failed", error_type=type(exc).__name__)
        raise RuntimeError("Token revocation store is unavailable") from exc


async def revoke(jti: str, expires_at: datetime) -> bool:
    if not jti or len(jti) > 64 or expires_at.tzinfo is None:
        logger.error("Invalid token revocation input")
        raise ValueError("Token revocation requires a valid JTI and timezone-aware expiry")
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return True
    try:
        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(TokenRevocation).where(TokenRevocation.expires_at <= now)
                )
                statement = insert(TokenRevocation).values(jti=jti, expires_at=expires_at)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[TokenRevocation.jti],
                        set_={"expires_at": statement.excluded.expires_at},
                        where=TokenRevocation.expires_at < statement.excluded.expires_at,
                    )
                )
        return True
    except Exception as exc:
        logger.error("Token revocation write failed", error_type=type(exc).__name__)
        return False
