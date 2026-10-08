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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
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


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str


def _reject_query_credentials(request: Request, *field_names: str) -> None:
    if any(field_name in request.query_params for field_name in field_names):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication values must be sent in the request body",
        )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Register a new user.

    Args:
        payload: User's email address, username, and plain-text password
        request: HTTP request, used to reject credential-bearing query parameters
        db: Database session

    Returns:
        Success message and user ID
    """
    # Validate password strength
    _reject_query_credentials(request, "email", "username", "password")

    is_valid, errors = check_password_strength(payload.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Password too weak", "errors": errors},
        )

    user_repo = UserRepository(db)

    # Check if email already exists
    existing_user = await user_repo.get_by_email(payload.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Check if username already exists
    existing_user = await user_repo.get_by_username(payload.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Create new user
    hashed_password = hash_password(payload.password)
    new_user = User(
        email=payload.email,
        username=payload.username,
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
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Login a user and return access + refresh tokens.

    Args:
        payload: User's email address and plain-text password
        request: HTTP request, used to reject credential-bearing query parameters
        db: Database session

    Returns:
        Access token, refresh token, and token type
    """
    user_repo = UserRepository(db)

    # Get user by email
    _reject_query_credentials(request, "email", "password")
    user = await user_repo.get_by_email(payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Verify password
    if not verify_password(payload.password, user.hashed_password):
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
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Refresh access token using refresh token.

    Args:
        payload: The refresh token
        request: HTTP request, used to reject credential-bearing query parameters
        db: Database session

    Returns:
        New access token
    """
    from features.auth.jwt import decode_token, REFRESH_TOKEN_TYPE, JWTError

    _reject_query_credentials(request, "refresh_token")
    try:
        # Decode refresh token
        token_payload = decode_token(payload.refresh_token)

        # Verify it's a refresh token
        if token_payload.get("type") != REFRESH_TOKEN_TYPE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # Get user
        user_id = token_payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )
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
    payload: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Request a password reset (sends email with reset token).

    Args:
        payload: User's email address
        request: HTTP request, used to reject query parameters
        db: Database session

    Returns:
        Success message (always success to prevent email enumeration)
    """
    _reject_query_credentials(request, "email")
    # TODO: Implement password reset token generation and email sending
    # For security, always return success even if email doesn't exist
    return {
        "message": "If the email exists, a password reset link has been sent"
    }


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Confirm password reset with token.

    Args:
        payload: Password reset token and new password
        request: HTTP request, used to reject credential-bearing query parameters
        db: Database session

    Returns:
        Success message
    """
    _reject_query_credentials(request, "token", "new_password")
    # TODO: Implement password reset token validation and password update
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Password reset not yet implemented",
    )
