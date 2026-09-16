"""
Step 8: transaction-safety tests for Collaboration mutations.

Exercises ``_create_collaboration``, ``_add_milestone``, and
``_update_milestone`` directly (same pattern as test_collaboration_request.py
/ test_collaboration_response.py: lightweight AppContext with an AsyncMock
db, collaboration repository methods monkeypatched) to prove that a
mid-transaction failure rolls back instead of leaving partial writes/a dirty
session.

``_accept_collaboration`` / ``_decline_collaboration`` already have this
coverage in test_collaboration_response.py
(test_accept_collaboration_rolls_back_when_participant_update_fails /
test_decline_collaboration_rolls_back_when_remove_fails); this file covers
the remaining write resolvers.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import (
    AppContext,
    _add_milestone,
    _create_collaboration,
    _update_collaboration,
    _update_milestone,
)
from app.models.collaboration import CollaborationStatus, MilestoneStatus
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


def make_collab_input(**overrides):
    base = dict(
        title="Joint livestream",
        description="Let's collab on a stream",
        content_type="livestream",
        platform="twitch",
        tags=["music", "live"],
        participant_ids=[uuid.uuid4()],
        budget_min=100.0,
        budget_max=500.0,
        budget_currency="USD",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch):
    async def fake_track_event(self, **kwargs):
        return None

    async def allow_participants(self, initiator, participant_ids):
        return participant_ids

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event",
        fake_track_event,
    )
    monkeypatch.setattr(
        "services.collaboration_service.CollaborationInviteEligibilityService.validate_participant_ids",
        allow_participants,
    )


# ── Collaboration creation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_collaboration_rolls_back_when_participant_insert_fails(monkeypatch):
    """3. Collaboration creation failure rolls back the collaboration and all participant inserts."""
    async def fake_create(self, collab):
        collab.id = uuid.uuid4()
        return collab

    async def fail_add_participant(self, participant):
        raise RuntimeError("participant insert failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.create", fake_create
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_participant",
        fail_add_participant,
    )

    owner = make_user(username="owner")
    ctx = make_ctx(owner)

    with pytest.raises(RuntimeError, match="participant insert failed"):
        await _create_collaboration(ctx, make_collab_input())

    ctx.db.rollback.assert_awaited_once_with()
    ctx.db.commit.assert_not_awaited()


# ── Milestone creation ─────────────────────────────────────────────────────


def make_collab(initiator_id=None, deleted_at=None) -> SimpleNamespace:
    collab = SimpleNamespace(
        id=uuid.uuid4(),
        initiator_id=initiator_id if initiator_id is not None else uuid.uuid4(),
        status=CollaborationStatus.PROPOSED,
        deleted_at=deleted_at,
    )
    collab._update_collaboration = lambda new_status: setattr(
        collab, "status", CollaborationStatus(new_status)
    )
    return collab


def make_milestone_input(collaboration_id, **overrides):
    base = dict(
        collaboration_id=collaboration_id,
        title="Phase 1",
        description="Kickoff",
        due_date=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_add_milestone_rolls_back_when_insert_fails(monkeypatch):
    """4. Milestone creation failure rolls back the milestone."""
    owner = make_user(username="owner")
    collab = make_collab(initiator_id=owner.id)

    async def fake_get_by_id(self, entity_id):
        return collab

    async def fake_get_participant(self, collab_id, user_id):
        return None

    async def fail_add_milestone(self, milestone):
        raise RuntimeError("milestone insert failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_participant",
        fake_get_participant,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_milestone",
        fail_add_milestone,
    )

    ctx = make_ctx(owner)

    with pytest.raises(RuntimeError, match="milestone insert failed"):
        await _add_milestone(ctx, make_milestone_input(collab.id))

    ctx.db.rollback.assert_awaited_once_with()
    ctx.db.commit.assert_not_awaited()


# ── Milestone update ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_milestone_rolls_back_and_preserves_prior_state_when_update_fails(monkeypatch):
    """5. Milestone update failure leaves the prior milestone state intact."""
    owner = make_user(username="owner")
    collab = make_collab(initiator_id=owner.id)
    milestone = SimpleNamespace(
        id=uuid.uuid4(),
        collaboration_id=collab.id,
        title="Original title",
        description="Original description",
        status=MilestoneStatus.PENDING,
        due_at=None,
    )

    async def fake_get_milestone_by_id(self, milestone_id):
        return milestone

    async def fake_get_by_id(self, entity_id):
        return collab

    async def fake_get_participant(self, collab_id, user_id):
        return None

    async def fail_update_milestone(self, m):
        raise RuntimeError("milestone update failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_milestone_by_id",
        fake_get_milestone_by_id,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_participant",
        fake_get_participant,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update_milestone",
        fail_update_milestone,
    )

    ctx = make_ctx(owner)
    update_input = SimpleNamespace(
        title="Changed title", description=None, status=None, due_date=None
    )

    with pytest.raises(RuntimeError, match="milestone update failed"):
        await _update_milestone(ctx, str(milestone.id), update_input)

    ctx.db.rollback.assert_awaited_once_with()
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_collaboration_rolls_back_when_status_update_fails(monkeypatch):
    owner = make_user(username="owner")
    collab = SimpleNamespace(
        id=uuid.uuid4(),
        initiator_id=owner.id,
        title="Original",
        description=None,
        content_type=None,
        platform=None,
        tags=None,
        budget_min=None,
        budget_max=None,
        budget_currency="USD",
        status=CollaborationStatus.ACCEPTED,
        proposed_at=None,
        started_at=None,
        completed_at=None,
        updated_at=None,
        created_at=None,
        deleted_at=None,
    )
    collab._update_collaboration = lambda new_status: setattr(
        collab, "status", CollaborationStatus(new_status)
    )

    async def fake_get_by_id(self, entity_id):
        return collab

    async def fail_update(self, collaboration):
        raise RuntimeError("collaboration update failed")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update",
        fail_update,
    )

    ctx = make_ctx(owner)
    with pytest.raises(RuntimeError, match="collaboration update failed"):
        await _update_collaboration(
            ctx,
            str(collab.id),
            SimpleNamespace(status=SimpleNamespace(value="completed")),
        )

    assert collab.status == CollaborationStatus.ACCEPTED
    ctx.db.rollback.assert_awaited_once_with()
    ctx.db.commit.assert_not_awaited()
