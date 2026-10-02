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
from unittest.mock import AsyncMock, Mock
from typing import cast

import pytest

from api.graphql import (
    AppContext,
    _accept_collaboration,
    _decline_collaboration,
    _update_collaboration,
)
from app.models.analytics import EventType
from app.models.collaboration import CollaborationStatus
from app.models.user import AccountStatus, User, UserRole
from services.analytics_event_service import AnalyticsEventService


_REAL_TRACK_EVENT = AnalyticsEventService.track_event


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


def make_collab(initiator_id=None, status=CollaborationStatus.PROPOSED, deleted_at=None) -> SimpleNamespace:
    collab = SimpleNamespace(
        id=uuid.uuid4(),
        initiator_id=initiator_id if initiator_id is not None else uuid.uuid4(),
        status=status,
        deleted_at=deleted_at,
        started_at=None,
    )
    collab._update_collaboration = lambda new_status: setattr(
        collab, "status", CollaborationStatus(new_status)
    )
    return collab


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch):
    calls = []

    async def fake_track_event(self, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event",
        fake_track_event,
    )
    return calls


def patch_repo(monkeypatch, participant, collab, pending_participants=None):
    """Patch the collaboration repository with deterministic doubles."""
    state = {
        "collab": collab,
        "removed": False,
        "removed_participant_ids": [],
        "add_participant_calls": 0,
    }

    async def fake_ensure_direct_conversation(self, collaboration, accepted_participant):
        state["messaging_bridge"] = (collaboration, accepted_participant)

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
        state["removed_participant_ids"].append(p.id)

    async def fake_get_pending_participants(self, c):
        return list(pending_participants or [])

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
        "repositories.collaboration_repository.CollaborationRepository.get_pending_participants",
        fake_get_pending_participants,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_participant",
        fake_add_participant,
    )
    monkeypatch.setattr(
        "services.collaboration_messaging_service.CollaborationMessagingService.ensure_direct_conversation",
        fake_ensure_direct_conversation,
    )
    return state


# ── Accept ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_collaboration_marks_participant_and_collaboration_accepted(monkeypatch, _stub_analytics):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    participant.collaboration_id = collab.id
    state = patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    result = await _accept_collaboration(ctx, collab.id)

    assert result.id == participant.id
    assert result.collaboration_id == collab.id
    assert result.user_id == participant.user_id
    assert result.role == participant.role
    assert result.accepted is True
    assert result.accepted_at is not None
    assert participant.accepted is True
    assert participant.accepted_at is not None
    assert collab.status == CollaborationStatus.ACCEPTED
    assert state["removed"] is False
    assert state["messaging_bridge"] == (collab, participant)
    from typing import cast
    from unittest.mock import AsyncMock

    cast(AsyncMock, ctx.db.commit).assert_awaited_once_with()
    # Accepting activates the existing participant row; it never inserts a
    # brand-new collaboration/participant record.
    assert state["add_participant_calls"] == 0
    assert len(_stub_analytics) == 1
    event = _stub_analytics[0]
    assert event["event_type"] == EventType.COLLAB_ACCEPTED
    assert event["user"] is user
    assert event["session_id"] == "sess-test"
    assert event["metadata"] == {"collaboration_id": str(collab.id)}
    assert collab.started_at is not None


@pytest.mark.asyncio
async def test_accept_collaboration_succeeds_when_analytics_recording_fails(monkeypatch, _stub_analytics):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    participant.collaboration_id = collab.id
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event",
        _REAL_TRACK_EVENT,
    )
    ctx.db.add = Mock()
    ctx.db.flush = AsyncMock(side_effect=RuntimeError("simulated DB failure"))

    result = await _accept_collaboration(ctx, collab.id)

    assert result.id == participant.id
    assert participant.accepted is True
    assert collab.status == CollaborationStatus.ACCEPTED
    ctx.db.flush.assert_awaited_once()
    cast(AsyncMock, ctx.db.commit).assert_awaited_once_with()
    cast(AsyncMock, ctx.db.rollback).assert_not_awaited()


@pytest.mark.asyncio
async def test_accept_collaboration_preserves_existing_started_at(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()

    existing_started_at = "2026-09-01T12:34:56+00:00"
    collab.started_at = existing_started_at

    patch_repo(monkeypatch, participant, collab)

    await _accept_collaboration(make_ctx(user), collab.id)

    assert collab.started_at == existing_started_at

@pytest.mark.asyncio
async def test_accepting_one_invitee_resolves_and_removes_remaining_pending_invites(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    remaining = [make_participant(uuid.uuid4()) for _ in range(3)]
    collab = make_collab()
    state = patch_repo(monkeypatch, participant, collab, pending_participants=remaining)

    await _accept_collaboration(make_ctx(user), collab.id)

    assert participant.accepted is True
    assert collab.status == CollaborationStatus.ACCEPTED
    assert set(state["removed_participant_ids"]) == {invitee.id for invitee in remaining}
    assert participant.id not in state["removed_participant_ids"]


# ── Decline ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decline_collaboration_removes_participant_and_marks_declined(monkeypatch, _stub_analytics):
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
    commit_mock: AsyncMock = ctx.db.commit  # type: ignore[assignment]
    commit_mock.assert_awaited_once_with()
    assert len(_stub_analytics) == 1
    event = _stub_analytics[0]
    assert event["event_type"] == EventType.COLLAB_DECLINED
    assert event["user"] is user
    assert event["session_id"] == "sess-test"
    assert event["metadata"] == {"collaboration_id": str(collab.id)}


@pytest.mark.asyncio
async def test_cancel_collaboration_tracks_cancelled_event(monkeypatch, _stub_analytics):
    user = make_user()
    collab = make_collab(initiator_id=user.id)
    collab.proposed_at = None
    collab.completed_at = None
    patch_repo(monkeypatch, None, collab)
    ctx = make_ctx(user)
    monkeypatch.setattr("api.graphql._collaboration_to_gql", lambda collaboration: collaboration)

    await _update_collaboration(
        ctx,
        str(collab.id),
        SimpleNamespace(status=SimpleNamespace(value="cancelled")),
    )

    assert _stub_analytics[0]["event_type"] == EventType.COLLAB_CANCELLED


@pytest.mark.asyncio
async def test_start_collaboration_tracks_started_event(monkeypatch, _stub_analytics):
    user = make_user()
    collab = make_collab(initiator_id=user.id, status=CollaborationStatus.ACCEPTED)
    collab.proposed_at = None
    collab.completed_at = None
    patch_repo(monkeypatch, None, collab)
    ctx = make_ctx(user)
    monkeypatch.setattr("api.graphql._collaboration_to_gql", lambda collaboration: collaboration)

    await _update_collaboration(
        ctx,
        str(collab.id),
        SimpleNamespace(status=SimpleNamespace(value="in_progress")),
    )

    assert collab.status == CollaborationStatus.IN_PROGRESS
    assert collab.started_at is not None
    assert len(_stub_analytics) == 1
    event = _stub_analytics[0]
    assert event["event_type"] == EventType.COLLAB_STARTED
    assert event["user"] is user
    assert event["session_id"] == "sess-test"
    assert event["metadata"] == {"collaboration_id": str(collab.id)}


@pytest.mark.asyncio
async def test_complete_collaboration_tracks_completed_event(monkeypatch, _stub_analytics):
    user = make_user()
    collab = make_collab(initiator_id=user.id)
    collab.proposed_at = None
    collab.completed_at = None
    patch_repo(monkeypatch, None, collab)
    ctx = make_ctx(user)
    monkeypatch.setattr("api.graphql._collaboration_to_gql", lambda collaboration: collaboration)

    await _update_collaboration(
        ctx,
        str(collab.id),
        SimpleNamespace(status=SimpleNamespace(value="completed")),
    )

    assert _stub_analytics[0]["event_type"] == EventType.COLLAB_COMPLETED


@pytest.mark.asyncio
async def test_declining_one_of_multiple_invitees_keeps_proposal_open(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    remaining = make_participant(uuid.uuid4())
    collab = make_collab()
    state = patch_repo(monkeypatch, participant, collab, pending_participants=[remaining])

    await _decline_collaboration(make_ctx(user), collab.id)

    assert state["removed"] is True
    assert collab.status == CollaborationStatus.PROPOSED


@pytest.mark.asyncio
async def test_declining_last_invitee_marks_collaboration_declined(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)

    await _decline_collaboration(make_ctx(user), collab.id)

    assert collab.status == CollaborationStatus.DECLINED


@pytest.mark.asyncio
async def test_accept_collaboration_rolls_back_when_participant_update_fails(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)

    async def fail_update_participant(self, p):
        raise RuntimeError("write failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update_participant",
        fail_update_participant,
    )
    ctx = make_ctx(user)

    with pytest.raises(RuntimeError, match="write failed"):
        await _accept_collaboration(ctx, collab.id)

    ctx.db.rollback.assert_awaited_once_with()  # pyright: ignore[reportAttributeAccessIssue]
    ctx.db.commit.assert_not_awaited()  # pyright: ignore[reportAttributeAccessIssue]
    assert collab.status == CollaborationStatus.PROPOSED


@pytest.mark.asyncio
async def test_accept_collaboration_rolls_back_when_collaboration_update_fails(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)

    async def fail_update(self, c):
        raise RuntimeError("update failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update",
        fail_update,
    )
    ctx = make_ctx(user)

    with pytest.raises(RuntimeError, match="update failed"):
        await _accept_collaboration(ctx, collab.id)

    ctx.db.rollback.assert_awaited_once_with()  # pyright: ignore[reportAttributeAccessIssue]
    ctx.db.commit.assert_not_awaited()  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_accept_collaboration_rolls_back_when_commit_fails(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)
    ctx.db.commit.side_effect = RuntimeError("commit failed")  # pyright: ignore[reportFunctionMemberAccess, reportAttributeAccessIssue]

    with pytest.raises(RuntimeError, match="commit failed"):
        await _accept_collaboration(ctx, collab.id)

    ctx.db.commit.assert_awaited_once_with()  # pyright: ignore[reportAttributeAccessIssue]
    ctx.db.rollback.assert_awaited_once_with()  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_accept_collaboration_rolls_back_when_messaging_bridge_fails(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)

    async def fail_ensure_direct_conversation(self, collaboration, accepted_participant):
        raise PermissionError("Cannot message a blocked user")

    monkeypatch.setattr(
        "services.collaboration_messaging_service.CollaborationMessagingService.ensure_direct_conversation",
        fail_ensure_direct_conversation,
    )
    ctx = make_ctx(user)

    with pytest.raises(PermissionError, match="blocked"):
        await _accept_collaboration(ctx, collab.id)

    ctx.db.rollback.assert_awaited_once_with()  # pyright: ignore[reportAttributeAccessIssue]
    ctx.db.commit.assert_not_awaited()  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_decline_collaboration_rolls_back_when_remove_fails(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab()
    patch_repo(monkeypatch, participant, collab)

    async def fail_remove_participant(self, p):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.remove_participant",
        fail_remove_participant,
    )
    ctx = make_ctx(user)

    with pytest.raises(RuntimeError, match="delete failed"):
        await _decline_collaboration(ctx, collab.id)

    ctx.db.rollback.assert_awaited_once_with()  # pyright: ignore[reportAttributeAccessIssue]
    ctx.db.commit.assert_not_awaited()  # pyright: ignore[reportAttributeAccessIssue]
    assert collab.status == CollaborationStatus.PROPOSED


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
async def test_accept_collaboration_rejects_soft_deleted_collaboration(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab(status=CollaborationStatus.PROPOSED, deleted_at="2024-01-01T00:00:00+00:00")
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="not found"):
        await _accept_collaboration(ctx, collab.id)


@pytest.mark.asyncio
async def test_accept_collaboration_requires_auth(monkeypatch):
    patch_repo(monkeypatch, None, make_collab())
    ctx = make_ctx(None)

    with pytest.raises(PermissionError):
        await _accept_collaboration(ctx, uuid.uuid4())


@pytest.mark.asyncio
async def test_decline_collaboration_rejects_soft_deleted_collaboration(monkeypatch):
    user = make_user()
    participant = make_participant(user.id)
    collab = make_collab(status=CollaborationStatus.PROPOSED, deleted_at="2024-01-01T00:00:00+00:00")
    patch_repo(monkeypatch, participant, collab)
    ctx = make_ctx(user)

    with pytest.raises(ValueError, match="not found"):
        await _decline_collaboration(ctx, collab.id)


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
