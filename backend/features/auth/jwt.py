"""
JWT token management for authentication.

Handles:
- Access token generation and validation
- Refresh token generation and validation
- Token blacklisting (for logout)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, status
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.config import settings
from app.models.user import User

# Token types
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


class JWTError(HTTPException):
    """Base exception for JWT-related errors."""

    def __init__(self, detail: str = "Could not validate credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_access_token(user: User, expires_delta: timedelta | None = None) -> str:
    """
    Create a JWT access token for a user.

    Args:
        user: The user to create a token for
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "username": user.username,
        "role": user.role.value,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),  # Unique token ID for blacklisting
    }

    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)


def create_refresh_token(user: User) -> str:
    """
    Create a JWT refresh token for a user.

    Args:
        user: The user to create a refresh token for

    Returns:
        Encoded JWT refresh token string
    """
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {
        "sub": str(user.id),
        "type": REFRESH_TOKEN_TYPE,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }

    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT token.

    Args:
        token: The JWT token string

    Returns:
        Decoded token payload

    Raises:
        JWTError: If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key.get_secret_value(), algorithms=[settings.jwt_algorithm]
        )
        return payload
    except ExpiredSignatureError:
        raise JWTError("Token has expired")
    except InvalidTokenError:
        raise JWTError("Invalid token")


def get_token_payload(token: str) -> dict[str, Any]:
    """
    Get the payload from a token without full validation (for blacklist checking).

    Args:
        token: The JWT token string

    Returns:
        Token payload or empty dict if invalid
    """
    try:
        # Decode without verification to get payload (for checking blacklist)
        payload = jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False}
        )
        return payload
    except Exception:
        return {}


async def is_token_blacklisted(jti: str) -> bool:
    """
    Check if a token is blacklisted.

    Args:
        jti: The JWT ID to check

    Returns:
        True if token is blacklisted, False otherwise
    """
    from services.redis_service import RedisService

    redis_service = RedisService()
    try:
        if not redis_service.redis:
            await redis_service.connect()
        return await redis_service.is_token_blacklisted(jti)
    except Exception as exc:
        raise RuntimeError("Token revocation store is unavailable") from exc
    finally:
        if redis_service.redis:
            try:
                await redis_service.disconnect()
            except Exception:
                pass


async def blacklist_token(jti: str, exp: datetime) -> bool:
    """
    Add a token to the blacklist.

    Args:
        jti: The JWT ID to blacklist
        exp: Token expiration time
    """
    from services.redis_service import RedisService

    redis_service = RedisService()
    try:
        if not redis_service.redis:
            await redis_service.connect()
        await redis_service.blacklist_token(jti, exp)
        return True
    except Exception:
        return False
    finally:
        if redis_service.redis:
            try:
                await redis_service.disconnect()
            except Exception:
                pass
