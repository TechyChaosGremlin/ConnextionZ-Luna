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
- The sender (initiator) cannot accept or decline their own request
- A user who is not a participant cannot accept or decline
- Invalid state transitions are rejected (already accepted / already declined / not pending)
- A second Accept call cannot create a duplicate collaboration/participant record
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


def make_participant(user_id, *, accepted=False, accepted_at=None, role="participant") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        collaboration_id=uuid.uuid4(),
        user_id=user_id,
        role=role,
        accepted=accepted,
        accepted_at=accepted_at,
    )


def make_collab(initiator_id=None, status=CollaborationStatus.PROPOSED) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        initiator_id=initiator_id if initiator_id is not None else uuid.uuid4(),
        status=status,
    )


def patch_repo(monkeypatch, participant, collab):
    """Patch the collaboration repository with deterministic doubles."""
    state = {"collab": collab, "removed": False, "add_participant_calls": 0}

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

    async def fake_add_participant(self, p):
        # Accept/decline must never create new participant rows; only
        # `_create_collaboration` should call this.
        state["add_participant_calls"] += 1
        return p

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_participant",
        fake_get_participant,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_by_id_for_update",
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
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_participant",
        fake_add_participant,
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
    # Accepting activates the existing participant row; it never inserts a
    # brand-new collaboration/participant record.
    assert state["add_participant_calls"] == 0


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


# ── Sender cannot act on their own request ────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_sender(monkeypatch):
    sender = make_user(username="sender")
    # The initiator's own participant row (created at proposal time).
    sender_participant = make_participant(sender.id, accepted=True, role="initiator")
    collab = make_collab(initiator_id=sender.id)
    patch_repo(monkeypatch, sender_participant, collab)
    ctx = make_ctx(sender)

    with pytest.raises(PermissionError, match="sender"):
        await _accept_collaboration(ctx, collab.id)

    assert collab.status == CollaborationStatus.PROPOSED


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_sender(monkeypatch):
    sender = make_user(username="sender")
    sender_participant = make_participant(sender.id, accepted=True, role="initiator")
    collab = make_collab(initiator_id=sender.id)
    patch_repo(monkeypatch, sender_participant, collab)
    ctx = make_ctx(sender)

    with pytest.raises(PermissionError, match="sender"):
        await _decline_collaboration(ctx, collab.id)

    assert collab.status == CollaborationStatus.PROPOSED


# ── Unauthorized / unrelated user ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_non_participant(monkeypatch):
    # Participant row belongs to another user, so this user is not authorized.
    user = make_user()
    other_participant = make_participant(uuid.uuid4())
    collab = make_collab()
    patch_repo(monkeypatch, other_participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(PermissionError, match="not a participant"):
        await _accept_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_non_participant(monkeypatch):
    user = make_user()
    collab = make_collab()
    patch_repo(monkeypatch, None, collab)  # no participant row for this user
    ctx = make_ctx(user)

    with pytest.raises(PermissionError, match="not a participant"):
        await _decline_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_accept_collaboration_requires_auth(monkeypatch):
    patch_repo(monkeypatch, None, make_collab())
    ctx = make_ctx(None)

    with pytest.raises(PermissionError):
        await _accept_collaboration(ctx, uuid.uuid4())


@pytest.mark.asyncio
async def test_decline_collaboration_requires_auth(monkeypatch):
    patch_repo(monkeypatch, None, make_collab())
    ctx = make_ctx(None)

    with pytest.raises(PermissionError):
        await _decline_collaboration(ctx, uuid.uuid4())


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_missing_collaboration(monkeypatch):
    user = make_user()
    patch_repo(monkeypatch, None, None)  # collaboration does not exist
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="not found"):
        await _accept_collaboration(ctx, uuid.uuid4())


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_missing_collaboration(monkeypatch):
    user = make_user()
    patch_repo(monkeypatch, None, None)  # collaboration does not exist
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="not found"):
        await _decline_collaboration(ctx, uuid.uuid4())


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


@pytest.mark.asyncio
async def test_accept_collaboration_rejects_already_declined_collaboration(monkeypatch):
    # After a real decline, the recipient's participant row is removed and
    # the collaboration status is DECLINED — resubmitting Accept must fail.
    user = make_user()
    collab = make_collab(status=CollaborationStatus.DECLINED)
    patch_repo(monkeypatch, None, collab)
    ctx = make_ctx(user)

    with pytest.raises(PermissionError, match="not a participant"):
        await _accept_collaboration(ctx, collab.id)

    assert collab.status == CollaborationStatus.DECLINED


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_already_declined_collaboration(monkeypatch):
    user = make_user()
    collab = make_collab(status=CollaborationStatus.DECLINED)
    patch_repo(monkeypatch, None, collab)
    ctx = make_ctx(user)

    with pytest.raises(PermissionError, match="not a participant"):
        await _decline_collaboration(ctx, collab.id)

    assert collab.status == CollaborationStatus.DECLINED


# ── Duplicate Accept cannot create duplicate collaboration state ─────────────


@pytest.mark.asyncio
async def test_duplicate_accept_requests_cannot_double_process(monkeypatch):
    """Simulates two sequential acceptCollaboration calls for the same request."""
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    state = patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    first = await _accept_collaboration(ctx, collab.id)
    assert first.accepted is True
    assert collab.status == CollaborationStatus.ACCEPTED

    with pytest.raises(ValueError, match="already been accepted"):
        await _accept_collaboration(ctx, collab.id)

    # No extra participant/collaboration rows were ever created.
    assert state["add_participant_calls"] == 0
