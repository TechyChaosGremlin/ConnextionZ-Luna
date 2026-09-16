"""Focused creator-discovery to collaboration-invite handoff tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import AppContext, _create_collaboration, _discover_creators
from app.models.user import AccountStatus, User, UserRole
from services.collaboration_service import CollaborationInviteEligibilityService


def make_user(username: str, *, status=AccountStatus.ACTIVE, deleted_at=None) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=uuid.uuid4(), email=f"{username}@example.test", username=username,
        hashed_password="hashed", role=UserRole.CREATOR, status=status,
        email_verified=True, mfa_enabled=False, created_at=now, updated_at=now,
        deleted_at=deleted_at,
    )


def make_profile(user: User, **overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        id=uuid.uuid4(), user_id=user.id, display_name=user.username, bio="creator",
        avatar_url=None, cover_image_url=None, website_url=None, location=None,
        social_links=None, tags=["music"], follower_count=0, following_count=0,
        collaboration_count=0, total_likes=0, open_to_collab=True,
        private_account=False, deleted_at=None, created_at=now, updated_at=now,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_input(participant_ids):
    return SimpleNamespace(
        title="Discovery handoff", description=None, content_type="video", platform="youtube",
        tags=["music"], participant_ids=participant_ids, budget_min=None,
        budget_max=None, budget_currency="USD",
    )


@pytest.fixture
def eligibility_dependencies(monkeypatch):
    initiator = make_user("initiator")
    target = make_user("target")
    state = {"target": target, "profile": make_profile(target), "restricted": set(), "follows": False}

    async def get_user(self, user_id):
        return state["target"] if user_id == state["target"].id else None

    async def get_profile(self, user_id):
        return state["profile"] if user_id == state["target"].id else None

    async def restricted_ids(self, initiator_id, target_ids):
        assert initiator_id == initiator.id
        return state["restricted"]

    async def is_following(self, follower_id, following_id):
        return state["follows"]

    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", get_user)
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_user_id", get_profile)
    monkeypatch.setattr("repositories.social_repository.FeedSafetyRepository.get_invitation_restricted_user_ids", restricted_ids)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.is_following", is_following)
    return initiator, state


@pytest.mark.asyncio
async def test_discover_creators_returns_declared_creator_card(monkeypatch, eligibility_dependencies):
    initiator, state = eligibility_dependencies

    async def get_all(self, limit):
        return [state["profile"]]

    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_all", get_all)
    result = await _discover_creators(AppContext(db=AsyncMock(), current_user=initiator), None, ["music"], 20, None)

    assert result.edges[0].node.user.id == state["target"].id
    assert result.edges[0].node.profile.user_id == state["target"].id


@pytest.mark.asyncio
async def test_discovered_creator_id_becomes_pending_collaboration_participant(monkeypatch, eligibility_dependencies):
    initiator, state = eligibility_dependencies
    participants = []

    async def get_all(self, limit):
        return [state["profile"]]

    async def create(self, collaboration):
        collaboration.id = uuid.uuid4()
        return collaboration

    async def add_participant(self, participant):
        participants.append(participant)
        return participant

    async def track_event(self, **kwargs):
        return None

    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_all", get_all)
    monkeypatch.setattr("repositories.collaboration_repository.CollaborationRepository.create", create)
    monkeypatch.setattr("repositories.collaboration_repository.CollaborationRepository.add_participant", add_participant)
    monkeypatch.setattr("services.analytics_event_service.AnalyticsEventService.track_event", track_event)
    ctx = AppContext(db=AsyncMock(), current_user=initiator, session_id="handoff")
    discovery = await _discover_creators(ctx, None, ["music"], 20, None)

    await _create_collaboration(ctx, make_input([discovery.edges[0].node.user.id]))

    invited = next(participant for participant in participants if participant.role == "participant")
    assert invited.user_id == state["target"].id
    assert invited.accepted is not True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_update", "error"),
    [
        ({"target": None}, "not found"),
        ({"target_status": AccountStatus.SUSPENDED}, "not active"),
        ({"target_deleted": "2026-01-01"}, "not found"),
        ({"restricted": "target"}, "block or mute"),
        ({"open_to_collab": False}, "not open"),
        ({"private_account": True}, "followers"),
    ],
)
async def test_invite_eligibility_rejects_ineligible_creators(eligibility_dependencies, state_update, error):
    initiator, state = eligibility_dependencies
    if state_update.get("target") is None and "target" in state_update:
        target_id = uuid.uuid4()
    else:
        target_id = state["target"].id
    if "target_status" in state_update:
        state["target"].status = state_update["target_status"]
    if "target_deleted" in state_update:
        state["target"].deleted_at = state_update["target_deleted"]
    if "restricted" in state_update:
        state["restricted"] = {state["target"].id}
    if "open_to_collab" in state_update:
        state["profile"].open_to_collab = state_update["open_to_collab"]
    if "private_account" in state_update:
        state["profile"].private_account = state_update["private_account"]

    with pytest.raises((ValueError, PermissionError), match=error):
        await CollaborationInviteEligibilityService(AsyncMock()).validate_participant_ids(initiator, [target_id])


@pytest.mark.asyncio
async def test_invite_eligibility_rejects_self_and_duplicate_ids(eligibility_dependencies):
    initiator, state = eligibility_dependencies
    service = CollaborationInviteEligibilityService(AsyncMock())

    with pytest.raises(ValueError, match="yourself"):
        await service.validate_participant_ids(initiator, [initiator.id])
    with pytest.raises(ValueError, match="Duplicate"):
        await service.validate_participant_ids(initiator, [state["target"].id, state["target"].id])