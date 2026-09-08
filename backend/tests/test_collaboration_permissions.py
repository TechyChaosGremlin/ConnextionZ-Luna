"""
Focused tests for collaboration permission rules.

Verifies authorization across the collaboration resolvers for the five
required cases, following the pattern established in test_social_interactions.py
(lightweight AppContext + monkeypatched collaboration repository):

- Collaboration initiator (may update; allowed on detail/milestone)
- Collaboration participant (may accept/decline; allowed on detail/milestone)
- Unrelated users (denied everywhere access is restricted)
- Authentication (required for all protected operations)
- Invalid collaboration states (accept/decline guarded by PROPOSED-only)

Covers the resolvers that carry permission checks:
_collect detail (``_collaboration``), update (``_update_collaboration``),
add/update milestone (``_add_milestone`` / ``_update_milestone``).
Accept/decline participant/state permission cases are covered in
test_collaboration_response.py; this file focuses on initiator/participant/
unrelated/auth and the milestone-update gate.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.graphql import (
    AppContext,
    _add_milestone,
    _collaboration,
    _update_collaboration,
    _update_milestone,
)
from app.models.collaboration import CollaborationStatus, MilestoneStatus
from app.models.user import AccountStatus, User, UserRole


def make_user(username: str = "alice", role: UserRole = UserRole.CREATOR) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{username}@example.com",
        username=username,
        hashed_password="hashed",
        role=role,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )


def make_ctx(user: User | None) -> AppContext:
    return AppContext(db=AsyncMock(), current_user=user, session_id="sess-test")


def make_collab(initiator_id, status=CollaborationStatus.PROPOSED) -> SimpleNamespace:
    collab = SimpleNamespace(
        id=uuid.uuid4(),
        initiator_id=initiator_id,
        status=status,
        title="t",
        description=None,
        content_type=None,
        platform=None,
        tags=None,
        budget_min=None,
        budget_max=None,
        budget_currency="USD",
        created_at=None,
        updated_at=None,
        proposed_at=None,
        started_at=None,
        completed_at=None,
    )
    return collab


def make_milestone(collab_id) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        collaboration_id=collab_id,
        title="m1",
        description=None,
        status=MilestoneStatus.PENDING,
        sort_order=0,
        due_at=None,
        completed_at=None,
        created_at=None,
        updated_at=None,
    )


def _uuid(value):
    """Normalize string ids (as supplied by GraphQL) back to UUIDs."""
    return uuid.UUID(str(value))


def patch_collab_repo(
    monkeypatch, *, collab=None, milestone=None, participant_user_id=None, participant_accepted=True
):
    """Patch collaboration repository accessors with deterministic doubles."""
    repo_path = "repositories.collaboration_repository.CollaborationRepository"

    async def fake_get_by_id(self, entity_id):
        if collab is not None and _uuid(entity_id) == collab.id:
            return collab
        return None

    async def fake_get_participant(self, collab_id, user_id):
        if participant_user_id is not None and user_id == participant_user_id:
            return SimpleNamespace(id=uuid.uuid4(), user_id=user_id, accepted=participant_accepted)
        return None

    async def fake_get_milestone_by_id(self, milestone_id):
        if milestone is not None and _uuid(milestone_id) == milestone.id:
            return milestone
        return None

    async def fake_update(self, c):
        return c

    async def fake_update_milestone(self, m):
        return m

    async def fake_add_milestone(self, m):
        return m

    monkeypatch.setattr(f"{repo_path}.get_by_id", fake_get_by_id)
    monkeypatch.setattr(f"{repo_path}.get_participant", fake_get_participant)
    monkeypatch.setattr(f"{repo_path}.get_milestone_by_id", fake_get_milestone_by_id)
    monkeypatch.setattr(f"{repo_path}.update", fake_update)
    monkeypatch.setattr(f"{repo_path}.update_milestone", fake_update_milestone)
    monkeypatch.setattr(f"{repo_path}.add_milestone", fake_add_milestone)
# ── Collaboration detail (_collaboration) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_detail_allows_initiator(monkeypatch):
    initiator = make_user(username="owner")
    collab = make_collab(initiator.id)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=initiator.id)

    result = await _collaboration(make_ctx(initiator), str(collab.id))

    assert result is not None
    assert result.initiator_id == initiator.id


@pytest.mark.asyncio
async def test_detail_allows_participant(monkeypatch):
    owner = make_user(username="owner")
    participant = make_user(username="member")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=participant.id)

    result = await _collaboration(make_ctx(participant), str(collab.id))

    assert result is not None


@pytest.mark.asyncio
async def test_detail_allows_pending_invitee_while_proposed(monkeypatch):
    """An invited recipient who hasn't responded yet can still view the invite."""
    owner = make_user(username="owner")
    invitee = make_user(username="invitee")
    collab = make_collab(owner.id, status=CollaborationStatus.PROPOSED)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=invitee.id, participant_accepted=False)

    result = await _collaboration(make_ctx(invitee), str(collab.id))

    assert result is not None


@pytest.mark.asyncio
async def test_detail_denies_unrelated_user(monkeypatch):
    owner = make_user(username="owner")
    outsider = make_user(username="outsider")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab)  # outsider is not a participant

    with pytest.raises(ValueError, match="Access denied"):
        await _collaboration(make_ctx(outsider), str(collab.id))


@pytest.mark.asyncio
async def test_detail_requires_auth(monkeypatch):
    collab = make_collab(uuid.uuid4())
    patch_collab_repo(monkeypatch, collab=collab)

    with pytest.raises(ValueError, match="Authentication required"):
        await _collaboration(make_ctx(None), str(collab.id))


# ── Update collaboration (_update_collaboration) ──────────────────────────────


@pytest.mark.asyncio
async def test_update_allows_initiator(monkeypatch):
    initiator = make_user(username="owner")
    collab = make_collab(initiator.id)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=initiator.id)

    result = await _update_collaboration(
        make_ctx(initiator), str(collab.id), SimpleNamespace(title="New title")
    )

    assert result.title == "New title"


@pytest.mark.asyncio
async def test_update_denies_participant(monkeypatch):
    owner = make_user(username="owner")
    participant = make_user(username="member")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=participant.id)

    with pytest.raises(PermissionError, match="initiator"):
        await _update_collaboration(
            make_ctx(participant), str(collab.id), SimpleNamespace(title="Hijack")
        )


@pytest.mark.asyncio
async def test_update_denies_unrelated_user_guessing_id(monkeypatch):
    """Merely knowing a valid collaboration ID must not grant update access."""
    owner = make_user(username="owner")
    outsider = make_user(username="outsider")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab)  # outsider has no participant row

    with pytest.raises(PermissionError, match="initiator"):
        await _update_collaboration(
            make_ctx(outsider), str(collab.id), SimpleNamespace(title="Hijack")
        )


@pytest.mark.asyncio
async def test_update_allows_admin(monkeypatch):
    admin = make_user(username="admin", role=UserRole.ADMIN)
    collab = make_collab(uuid.uuid4())
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=admin.id)

    result = await _update_collaboration(
        make_ctx(admin), str(collab.id), SimpleNamespace(title="Admin edit")
    )

    assert result.title == "Admin edit"


@pytest.mark.asyncio
async def test_update_requires_auth(monkeypatch):
    collab = make_collab(uuid.uuid4())
    patch_collab_repo(monkeypatch, collab=collab)

    with pytest.raises(PermissionError):
        await _update_collaboration(make_ctx(None), str(collab.id), SimpleNamespace())
# ── Add milestone (_add_milestone) ────────────────────────────────────────────


def _milestone_input(collab_id):
    return SimpleNamespace(
        collaboration_id=collab_id, title="M", description=None, due_date=None
    )


@pytest.mark.asyncio
async def test_add_milestone_allows_initiator(monkeypatch):
    owner = make_user(username="owner")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=owner.id)

    result = await _add_milestone(make_ctx(owner), _milestone_input(collab.id))

    assert result.title == "M"


@pytest.mark.asyncio
async def test_add_milestone_allows_participant(monkeypatch):
    owner = make_user(username="owner")
    participant = make_user(username="member")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab, participant_user_id=participant.id)

    result = await _add_milestone(make_ctx(participant), _milestone_input(collab.id))

    assert result.title == "M"


@pytest.mark.asyncio
async def test_add_milestone_denies_unrelated_user(monkeypatch):
    owner = make_user(username="owner")
    outsider = make_user(username="outsider")
    collab = make_collab(owner.id)
    patch_collab_repo(monkeypatch, collab=collab)  # outsider is not a participant

    with pytest.raises(PermissionError, match="Not a participant"):
        await _add_milestone(make_ctx(outsider), _milestone_input(collab.id))


@pytest.mark.asyncio
async def test_add_milestone_denies_pending_invitee(monkeypatch):
    """An invited recipient who hasn't accepted yet has no collaborator access."""
    owner = make_user(username="owner")
    invitee = make_user(username="invitee")
    collab = make_collab(owner.id)
    patch_collab_repo(
        monkeypatch, collab=collab, participant_user_id=invitee.id, participant_accepted=False
    )

    with pytest.raises(PermissionError, match="Not a participant"):
        await _add_milestone(make_ctx(invitee), _milestone_input(collab.id))
# ── Update milestone (_update_milestone) ──────────────────────────────────────


def _milestone_update_input():
    return SimpleNamespace(title="Updated", description=None, status=None, due_date=None)


@pytest.mark.asyncio
async def test_update_milestone_allows_participant(monkeypatch):
    owner = make_user(username="owner")
    participant = make_user(username="member")
    collab = make_collab(owner.id)
    milestone = make_milestone(collab.id)
    patch_collab_repo(monkeypatch, collab=collab, milestone=milestone, participant_user_id=participant.id)

    result = await _update_milestone(
        make_ctx(participant), str(milestone.id), _milestone_update_input()
    )

    assert result.title == "Updated"


@pytest.mark.asyncio
async def test_update_milestone_allows_initiator(monkeypatch):
    owner = make_user(username="owner")
    collab = make_collab(owner.id)
    milestone = make_milestone(collab.id)
    patch_collab_repo(monkeypatch, collab=collab, milestone=milestone, participant_user_id=owner.id)

    result = await _update_milestone(
        make_ctx(owner), str(milestone.id), _milestone_update_input()
    )

    assert result.title == "Updated"


@pytest.mark.asyncio
async def test_update_milestone_denies_unrelated_user(monkeypatch):
    owner = make_user(username="owner")
    outsider = make_user(username="outsider")
    collab = make_collab(owner.id)
    milestone = make_milestone(collab.id)
    patch_collab_repo(monkeypatch, collab=collab, milestone=milestone)  # outsider not a participant

    with pytest.raises(PermissionError, match="Not a participant"):
        await _update_milestone(make_ctx(outsider), str(milestone.id), _milestone_update_input())


@pytest.mark.asyncio
async def test_update_milestone_requires_auth(monkeypatch):
    collab = make_collab(uuid.uuid4())
    milestone = make_milestone(collab.id)
    patch_collab_repo(monkeypatch, collab=collab, milestone=milestone)

    with pytest.raises(PermissionError):
        await _update_milestone(make_ctx(None), str(milestone.id), _milestone_update_input())


@pytest.mark.asyncio
async def test_update_milestone_denies_pending_invitee(monkeypatch):
    owner = make_user(username="owner")
    invitee = make_user(username="invitee")
    collab = make_collab(owner.id)
    milestone = make_milestone(collab.id)
    patch_collab_repo(
        monkeypatch,
        collab=collab,
        milestone=milestone,
        participant_user_id=invitee.id,
        participant_accepted=False,
    )

    with pytest.raises(PermissionError, match="Not a participant"):
        await _update_milestone(make_ctx(invitee), str(milestone.id), _milestone_update_input())