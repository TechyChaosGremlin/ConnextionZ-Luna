from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, LoginInput, RegisterInput, _login, _register
from app.models.user import AccountStatus, User
from features.auth import router as auth_router
from repositories.user_repository import UserRepository


class InMemoryUserRepository:
    users_by_email: dict[str, User] = {}

    def __init__(self, db):
        pass

    async def get_by_email(self, email):
        return self.users_by_email.get(email)

    async def get_by_username(self, username):
        return next(
            (user for user in self.users_by_email.values() if user.username == username),
            None,
        )

    async def create(self, user):
        user.id = uuid.uuid4()
        user.created_at = datetime.now(timezone.utc)
        user.updated_at = user.created_at
        user.email_verified = False
        self.users_by_email[user.email] = user


@pytest.fixture(autouse=True)
def stub_registration_dependencies(monkeypatch):
    InMemoryUserRepository.users_by_email = {}
    monkeypatch.setattr("repositories.user_repository.UserRepository", InMemoryUserRepository)
    monkeypatch.setattr(auth_router, "UserRepository", InMemoryUserRepository)
    monkeypatch.setattr("features.auth.password.hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr("features.auth.password.check_password_strength", lambda password: (True, []))
    monkeypatch.setattr("features.auth.password.verify_password", lambda password, hashed: hashed == f"hash:{password}")
    monkeypatch.setattr(auth_router, "hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(auth_router, "check_password_strength", lambda password: (True, []))
    monkeypatch.setattr(auth_router, "verify_password", lambda password, hashed: hashed == f"hash:{password}")


@pytest.mark.asyncio
async def test_graphql_signup_returns_active_unverified_user_that_can_log_in():
    ctx = AppContext(db=AsyncMock())
    signup = await _register(
        ctx,
        RegisterInput(email="graphql@example.com", username="graphql", password="StrongPass123!"),
    )

    assert signup.user.status.value == AccountStatus.ACTIVE.value
    assert signup.user.email_verified is False

    login = await _login(ctx, LoginInput(email="graphql@example.com", password="StrongPass123!"))
    assert login.user.id == signup.user.id


@pytest.mark.asyncio
async def test_rest_signup_returns_active_unverified_user_that_can_log_in():
    db = AsyncMock()
    signup = await auth_router.register(
        email="rest@example.com",
        username="restuser",
        password="StrongPass123!",
        db=db,
    )

    assert signup["status"] == AccountStatus.ACTIVE.value
    user = InMemoryUserRepository.users_by_email["rest@example.com"]
    assert user.email_verified is False

    login = await auth_router.login(
        email="rest@example.com",
        password="StrongPass123!",
        db=db,
    )
    assert login["access_token"]