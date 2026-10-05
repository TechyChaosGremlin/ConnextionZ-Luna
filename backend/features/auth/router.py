"""
Authentication API routes.

Provides endpoints for:
- User registration
- User login
- Token refresh
- User logout
- Password reset (request and confirm)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db_session
from app.models.user import User, UserRole, AccountStatus
from features.auth.jwt import (
    create_access_token,
    create_refresh_token,
    blacklist_token,
)
from features.auth.password import hash_password, verify_password, check_password_strength
from repositories.user_repository import UserRepository

router = APIRouter(prefix="/auth", tags=["authentication"])
security = HTTPBearer()


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    email: str,
    username: str,
    password: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Register a new user.

    Args:
        email: User's email address
        username: User's username
        password: User's plain-text password
        db: Database session

    Returns:
        Success message and user ID
    """
    # Validate password strength
    is_valid, errors = check_password_strength(password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Password too weak", "errors": errors},
        )

    user_repo = UserRepository(db)

    # Check if email already exists
    existing_user = await user_repo.get_by_email(email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Check if username already exists
    existing_user = await user_repo.get_by_username(username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Create new user
    hashed_password = hash_password(password)
    new_user = User(
        email=email,
        username=username,
        hashed_password=hashed_password,
        role=UserRole.USER,  # Default role
        status=AccountStatus.ACTIVE,
    )

    await user_repo.create(new_user)
    await db.commit()

    return {
        "message": "User registered successfully",
        "user_id": str(new_user.id),
        "status": "active",
    }


@router.post("/login")
async def login(
    email: str,
    password: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Login a user and return access + refresh tokens.

    Args:
        email: User's email address
        password: User's plain-text password
        db: Database session

    Returns:
        Access token, refresh token, and token type
    """
    user_repo = UserRepository(db)

    # Get user by email
    user = await user_repo.get_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Verify password
    if not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Check account status
    if user.status != AccountStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {user.status.value}",
        )

    # Create tokens
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)

    # Create session in Redis (placeholder)
    # session_id = await redis_service.create_session(str(user.id))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


@router.post("/refresh")
async def refresh(
    refresh_token: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Refresh access token using refresh token.

    Args:
        refresh_token: The refresh token
        db: Database session

    Returns:
        New access token
    """
    from features.auth.jwt import decode_token, REFRESH_TOKEN_TYPE, JWTError

    try:
        # Decode refresh token
        payload = decode_token(refresh_token)

        # Verify it's a refresh token
        if payload.get("type") != REFRESH_TOKEN_TYPE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # Get user
        user_id = payload.get("sub")
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(user_id)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        # Create new access token
        access_token = create_access_token(user)

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.jwt_access_token_expire_minutes * 60,
        }

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )


@router.post("/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Logout user by blacklisting their current token.

    Args:
        credentials: HTTP Bearer credentials
        db: Database session

    Returns:
        Success message
    """
    from features.auth.jwt import ACCESS_TOKEN_TYPE, JWTError, blacklist_token, decode_token

    token = credentials.credentials
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    jti = payload.get("jti")
    exp = payload.get("exp")

    if not jti or not exp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    if not await blacklist_token(jti, datetime.fromtimestamp(float(exp), timezone.utc)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Logout could not revoke this token. Try again.",
        )

    return {"message": "Logged out successfully"}


@router.post("/password-reset/request")
async def request_password_reset(
    email: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Request a password reset (sends email with reset token).

    Args:
        email: User's email address
        db: Database session

    Returns:
        Success message (always success to prevent email enumeration)
    """
    # TODO: Implement password reset token generation and email sending
    # For security, always return success even if email doesn't exist
    return {
        "message": "If the email exists, a password reset link has been sent"
    }


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    token: str,
    new_password: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Confirm password reset with token.

    Args:
        token: Password reset token
        new_password: New password
        db: Database session

    Returns:
        Success message
    """
    # TODO: Implement password reset token validation and password update
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Password reset not yet implemented",
    )

