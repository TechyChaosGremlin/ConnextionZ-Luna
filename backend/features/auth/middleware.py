"""
FastAPI authentication middleware and dependencies.

Provides:
- get_current_user dependency for protected routes
- get_current_active_user dependency (checks if user is active)
- get_current_admin_user dependency (checks if user is admin)
- JWT token extraction from Authorization header
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db_session
from app.models.user import User, AccountStatus, UserRole
from features.auth.jwt import ACCESS_TOKEN_TYPE, decode_token, JWTError, is_token_blacklisted
from repositories.user_repository import UserRepository

# HTTP Bearer security scheme
security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """
    FastAPI dependency to get the current authenticated user from JWT token.

    Args:
        credentials: HTTP Bearer credentials from Authorization header
        db: Database session

    Returns:
        The authenticated User object

    Raises:
        HTTPException: If token is invalid or user not found
    """
    try:
        token = credentials.credentials
        payload = decode_token(token)

        if payload.get("type") != ACCESS_TOKEN_TYPE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if payload.get("type") == "access":
            jti = payload.get("jti")
            if jti and await is_token_blacklisted(jti):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(user_id)

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e.detail),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    FastAPI dependency to get the current active user.

    Args:
        current_user: The authenticated user from get_current_user

    Returns:
        The authenticated User object if active

    Raises:
        HTTPException: If user account is not active
    """
    if current_user.status != AccountStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )
    return current_user


async def get_current_admin_user(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """
    FastAPI dependency to get the current admin user.

    Args:
        current_user: The authenticated active user

    Returns:
        The authenticated User object if admin

    Raises:
        HTTPException: If user is not an admin
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_current_creator_user(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """
    FastAPI dependency to get the current creator or admin user.

    Args:
        current_user: The authenticated active user

    Returns:
        The authenticated User object if creator or admin

    Raises:
        HTTPException: If user is not a creator or admin
    """
    if current_user.role not in [UserRole.CREATOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator access required",
        )
    return current_user
