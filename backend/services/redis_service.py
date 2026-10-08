"""
Redis service for session management and caching.

Provides:
- Redis connection pool management
- Session CRUD operations
- Cache decorators
- Token blacklist management
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from app.config import settings


class RedisService:
    """
    Redis service for session and cache management.

    Features:
    - Connection pooling
    - Session CRUD operations
    - Cache with TTL
    - Token blacklisting
    """

    def __init__(self):
        """Initialize Redis connection pool."""
        self.redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Establish Redis connection pool."""
        self.redis = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.redis_max_connections,
        )

    async def disconnect(self) -> None:
        """Close Redis connection pool."""
        if self.redis:
            await self.redis.aclose()

    async def ping(self) -> bool:
        """Check Redis connectivity."""
        if not self.redis:
            return False
        try:
            return await self.redis.ping()
        except Exception:
            return False

    # ── Session Management ───────────────────────────────────────

    async def create_session(
        self,
        user_id: str,
        ttl: timedelta | None = None,
    ) -> str:
        """
        Create a new session and return session ID.

        Args:
            user_id: The user ID to create session for
            ttl: Session time-to-live (default from settings)

        Returns:
            Session ID (UUID string)
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        session_id = str(uuid.uuid4())
        if ttl is None:
            ttl = timedelta(seconds=settings.session_ttl_seconds)

        session_data = {
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "last_accessed": datetime.now().isoformat(),
        }

        # Store session with TTL
        await self.redis.setex(
            f"session:{session_id}",
            int(ttl.total_seconds()),
            json.dumps(session_data),
        )

        # Add session to user's session set
        await self.redis.sadd(f"user_sessions:{user_id}", session_id)

        return session_id

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """
        Get session data by session ID.

        Args:
            session_id: The session ID to retrieve

        Returns:
            Session data dict or None if not found
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        data = await self.redis.get(f"session:{session_id}")
        if data:
            # Update last accessed time
            session_data = json.loads(data)
            session_data["last_accessed"] = datetime.now().isoformat()
            await self.redis.set(
                f"session:{session_id}",
                json.dumps(session_data),
                keepttl=True,  # Preserve TTL
            )
            return session_data
        return None

    async def delete_session(self, session_id: str) -> None:
        """
        Delete a session by session ID.

        Args:
            session_id: The session ID to delete
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        # Get session to find user_id
        data = await self.redis.get(f"session:{session_id}")
        if data:
            session_data = json.loads(data)
            user_id = session_data.get("user_id")

            # Remove from user's session set
            if user_id:
                await self.redis.srem(f"user_sessions:{user_id}", session_id)

        # Delete session
        await self.redis.delete(f"session:{session_id}")

    async def delete_user_sessions(self, user_id: str) -> None:
        """
        Delete all sessions for a user.

        Args:
            user_id: The user ID to delete sessions for
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        # Get all session IDs for user
        session_ids = await self.redis.smembers(f"user_sessions:{user_id}")

        # Delete all sessions
        for session_id in session_ids:
            await self.redis.delete(f"session:{session_id}")

        # Clear user's session set
        await self.redis.delete(f"user_sessions:{user_id}")

    async def get_user_sessions(self, user_id: str) -> list[str]:
        """
        Get all active session IDs for a user.

        Args:
            user_id: The user ID to get sessions for

        Returns:
            List of session IDs
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        return list(await self.redis.smembers(f"user_sessions:{user_id}"))

    # ── Token Blacklist ──────────────────────────────────────────

    async def blacklist_token(self, jti: str, exp: datetime) -> None:
        """
        Add a token to the blacklist.

        Args:
            jti: JWT ID to blacklist
            exp: Token expiration time
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        now = datetime.now(timezone.utc)
        ttl = int((exp - now).total_seconds())
        if ttl > 0:
            await self.redis.set(f"blacklist:{jti}", "1", ex=ttl)

    async def is_token_blacklisted(self, jti: str) -> bool:
        """
        Check if a token is blacklisted.

        Args:
            jti: JWT ID to check

        Returns:
            True if token is blacklisted
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        return bool(await self.redis.exists(f"blacklist:{jti}"))

    # ── Cache Operations ─────────────────────────────────────────

    async def cache_set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """
        Set a cache value with optional TTL.

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds (default from settings)
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        if ttl is None:
            ttl = settings.cache_ttl_seconds

        await self.redis.setex(key, ttl, json.dumps(value))

    async def cache_get(self, key: str) -> Any | None:
        """
        Get a cached value.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def cache_delete(self, key: str) -> None:
        """
        Delete a cached value.

        Args:
            key: Cache key
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        await self.redis.delete(key)

    async def cache_clear_pattern(self, pattern: str) -> None:
        """
        Delete all cache keys matching a pattern.

        Args:
            pattern: Redis key pattern (e.g., "user:*")
        """
        if not self.redis:
            raise RuntimeError("Redis not connected")

        keys = []
        async for key in self.redis.scan_iter(match=pattern):
            keys.append(key)

        if keys:
            await self.redis.delete(*keys)


# Decorator for caching function results
def cached(ttl: int | None = None, key_prefix: str = "cache"):
    """
    Decorator to cache function results in Redis.

    Args:
        ttl: Time-to-live in seconds
        key_prefix: Prefix for cache keys
    """

    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Generate cache key from function name and arguments
            cache_key = f"{key_prefix}:{func.__name__}:{str(args)}:{str(kwargs)}"

            # Try to get from cache
            redis_service = RedisService()
            cached_value = await redis_service.cache_get(cache_key)
            if cached_value is not None:
                return cached_value

            # Execute function
            result = await func(*args, **kwargs)

            # Cache result
            await redis_service.cache_set(cache_key, result, ttl)

            return result

        return wrapper

    return decorator
