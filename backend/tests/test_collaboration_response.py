"""
Focused tests for accepting and declining a collaboration request.

Exercises the ``acceptCollaboration`` and ``declineCollaboration`` GraphQL
mutation resolvers (``_accept_collaboration`` / ``_decline_collaboration``)
directly, following the pattern established in test_social_interactions.py:
the resolvers are invoked with a lightweight AppContext and the collaboration
repository methods are monkeypatched so no real (Postgres-only) database is
required.

Covers:
- Accepting an invitation marks the participant accepted and the collaboration accepted
- Declining an invitation removes the participant and marks the collaboration declined
- A user who is not a participant cannot accept or decline
- Invalid state transitions are rejected (already accepted / not pending)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import (
    AppContext,
    _accept_collaboration,
    _decline_collaboration,
)
from app.models.collaboration import CollaborationStatus
from app.models.user import AccountStatus, User, UserRole


def make_user(username: str = "alice") -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{username}@example.com",
        username=username,
        hashed_password="hashed",
        role=UserRole.CREATOR,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )


def make_ctx(user: User | None) -> AppContext:
    return AppContext(db=AsyncMock(), current_user=user, session_id="sess-test")


def make_participant(user_id, *, accepted=False, accepted_at=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        collaboration_id=uuid.uuid4(),
        user_id=user_id,
        role="participant",
        accepted=accepted,
        accepted_at=accepted_at,
    )


def make_collab(status=CollaborationStatus.PROPOSED) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), status=status)


def patch_repo(monkeypatch, participant, collab):
    """Patch the collaboration repository with deterministic doubles."""
    state = {"collab": collab, "removed": False}

    async def fake_get_participant(self, collab_id, user_id):
        if participant is None:
            return None
        return participant if participant.user_id == user_id else None

    async def fake_get_by_id(self, entity_id):
        return collab

    async def fake_update_participant(self, p):
        return p

    async def fake_update(self, c):
        return c

    async def fake_remove_participant(self, p):
        state["removed"] = True

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_participant",
        fake_get_participant,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update_participant",
        fake_update_participant,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update",
        fake_update,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.remove_participant",
        fake_remove_participant,
    )
    return state
# ── Accept ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_collaboration_marks_participant_and_collaboration_accepted(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    state = patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    result = await _accept_collaboration(ctx, collab.id)

    assert result.user_id == user.id
    assert result.accepted is True
    assert participant.accepted is True
    assert participant.accepted_at is not None
    assert collab.status == CollaborationStatus.ACCEPTED
    assert state["removed"] is False


# ── Decline ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decline_collaboration_removes_participant_and_marks_declined(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    state = patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    result = await _decline_collaboration(ctx, collab.id)

    assert result is True
    assert state["removed"] is True
    assert collab.status == CollaborationStatus.DECLINED
    assert participant.accepted is False
# ── Unauthorized user ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_non_participant(monkeypatch):
    # Participant row belongs to another user, so this user is not authorized.
    user = make_user()
    other_participant = make_participant(uuid.uuid4())
    collab = make_collab()
    patch_repo(monkeypatch, other_participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="not a participant"):
        await _accept_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_non_participant(monkeypatch):
    user = make_user()
    collab = make_collab()
    patch_repo(monkeypatch, None, collab)  # no participant row for this user
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="not a participant"):
        await _decline_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_accept_collaboration_requires_auth(monkeypatch):
    patch_repo(monkeypatch, None, make_collab())
    ctx = make_ctx(None)

    with pytest.raises(PermissionError):
        await _accept_collaboration(ctx, uuid.uuid4())


# ── Invalid state transitions ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_already_accepted_invitation(monkeypatch):
    user = make_user()
    participant = make_participant(user.id, accepted=True)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="already been accepted"):
        await _accept_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_non_proposed_collaboration(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab(status=CollaborationStatus.ACCEPTED)
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="no longer pending"):
        await _accept_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_already_accepted_invitation(monkeypatch):
    user = make_user()
    participant = make_participant(user.id, accepted=True)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="cannot be declined"):
        await _decline_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_non_proposed_collaboration(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab(status=CollaborationStatus.CANCELLED)
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="no longer pending"):
        await _decline_collaboration(ctx, collab.id)