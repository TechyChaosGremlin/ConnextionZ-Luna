"""
Focused tests for creating/sending a collaboration request.

Exercises the ``createCollaboration`` GraphQL mutation resolver
(``_create_collaboration``) directly, following the pattern established in
test_social_interactions.py: the resolver is invoked with a lightweight
AppContext and the collaboration repository methods are monkeypatched so no
real (Postgres-only) database is required.

Covers:
- Creating a collaboration request links invited participants and the accepted initiator
- The initiator is not added twice when they are also listed in participant_ids
- A ``COLLAB_CREATED`` analytics event is tracked
- Authentication is required
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, _create_collaboration
from app.models.analytics import EventType
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


def make_input(**overrides):
    base = dict(
        title="Joint livestream",
        description="Let's collab on a stream",
        content_type="livestream",
        platform="twitch",
        tags=["music", "live"],
        participant_ids=[],
        budget_min=100.0,
        budget_max=500.0,
        budget_currency="USD",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch):
    """Capture analytics track_event calls and swallow them."""
    calls = []

    async def fake_track_event(self, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_event",
        fake_track_event,
    )
    return calls


@pytest.fixture
def repo_participants(monkeypatch):
    """Capture created participants and assign an id to created collaborations."""
    participants = []

    async def fake_create(self, collab):
        collab.id = uuid.uuid4()
        return collab

    async def fake_add_participant(self, participant):
        participant.id = uuid.uuid4()
        participants.append(participant)
        return participant

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.create", fake_create
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_participant",
        fake_add_participant,
    )
    return participants


@pytest.mark.asyncio
async def test_create_collaboration_creates_collaboration_with_participants_and_initiator(
    repo_participants,
):
    owner = make_user(username="owner")
    invited = uuid.uuid4()
    ctx = make_ctx(owner)

    result = await _create_collaboration(
        ctx,
        make_input(participant_ids=[invited]),
    )

    assert result.id is not None
    assert result.initiator_id == owner.id
    assert result.title == "Joint livestream"
    assert result.description == "Let's collab on a stream"
    assert result.content_type == "livestream"
    assert result.platform == "twitch"
    assert result.tags == ["music", "live"]
    assert result.budget_min == 100.0
    assert result.budget_max == 500.0
    assert result.budget_currency == "USD"
    # The GraphQL CollaborationType.status is the strawberry enum; compare by value.
    assert result.status.value == CollaborationStatus.PROPOSED.value

    # One pending invited participant plus the accepted initiator.
    assert len(repo_participants) == 2
    invited_p, initiator_p = repo_participants

    assert invited_p.user_id == invited
    assert invited_p.role == "participant"
    # Invited participants are created unmarked; SQLAlchemy applies the
    # `accepted=False` column default at flush/INSERT time.
    assert invited_p.accepted is not True

    assert initiator_p.user_id == owner.id
    assert initiator_p.role == "initiator"
    assert initiator_p.accepted is True
    assert initiator_p.accepted_at is not None


@pytest.mark.asyncio
async def test_create_collaboration_does_not_duplicate_initiator(repo_participants):
    owner = make_user(username="owner")
    other = uuid.uuid4()
    ctx = make_ctx(owner)

    await _create_collaboration(
        ctx,
        make_input(participant_ids=[other, owner.id]),  # owner listed too
    )

    # Initiator is added exactly once: only the "other" pending participant + initiator.
    assert len(repo_participants) == 2
    assert sorted(p.user_id for p in repo_participants) == sorted([owner.id, other])
    assert sum(1 for p in repo_participants if p.role == "initiator") == 1


@pytest.mark.asyncio
async def test_create_collaboration_tracks_collab_created_event(repo_participants, _stub_analytics):
    owner = make_user()
    ctx = make_ctx(owner)

    await _create_collaboration(
        ctx,
        make_input(participant_ids=[uuid.uuid4()]),
    )

    assert len(_stub_analytics) == 1
    event = _stub_analytics[0]
    assert event["event_type"] == EventType.COLLAB_CREATED
    assert event["user"] is owner
    assert event["session_id"] == "sess-test"
    assert "collaboration_id" in event["metadata"]


@pytest.mark.asyncio
async def test_create_collaboration_requires_auth(repo_participants):
    ctx = make_ctx(None)

    with pytest.raises(PermissionError):
        await _create_collaboration(ctx, make_input(participant_ids=[uuid.uuid4()]))

    assert repo_participants == []