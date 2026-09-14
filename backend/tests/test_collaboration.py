"""Unit tests for collaboration participant workflows."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.models.collaboration import (
    Collaboration,
    CollaborationParticipant,
    CollaborationStatus,
)
from repositories.collaboration_repository import CollaborationRepository
from services.collaboration_service import CollaborationService


def make_collaboration(
    status: CollaborationStatus = CollaborationStatus.PROPOSED,
) -> Collaboration:
    return Collaboration(
        id=uuid.uuid4(),
        initiator_id=uuid.uuid4(),
        title="Test collaboration",
        status=status,
    )


def make_service() -> tuple[CollaborationService, AsyncMock]:
    mock_repository = AsyncMock(spec=CollaborationRepository)
    repository = cast(CollaborationRepository, mock_repository)
    return CollaborationService(repository), mock_repository


@pytest.mark.asyncio
async def test_accept_participant():
    collaboration = make_collaboration()
    user_id = uuid.uuid4()
    participant = CollaborationParticipant(
        collaboration_id=collaboration.id,
        user_id=user_id,
    )
    service, repository = make_service()
    result = await service.accept_participant(collaboration, participant)

    assert result is participant
    assert participant.accepted is True
    assert participant.accepted_at is not None
    assert collaboration.status == CollaborationStatus.ACCEPTED
    repository.update_participant.assert_awaited_once_with(participant)
    repository.update.assert_awaited_once_with(collaboration)


@pytest.mark.asyncio
async def test_decline_participant():
    collaboration = make_collaboration()
    user_id = uuid.uuid4()
    participant = CollaborationParticipant(
        collaboration_id=collaboration.id,
        user_id=user_id,
    )
    service, repository = make_service()
    result = await service.decline_participant(collaboration, participant)

    assert result is True
    assert collaboration.status == CollaborationStatus.DECLINED
    repository.remove_participant.assert_awaited_once_with(participant)
    repository.update.assert_awaited_once_with(collaboration)


def test_invalid_status_transition():
    collaboration = make_collaboration(CollaborationStatus.ACCEPTED)

    with pytest.raises(ValueError, match="Invalid status transition"):
        collaboration._update_collaboration(CollaborationStatus.PROPOSED)


@pytest.mark.parametrize(
    ("current_status", "next_status"),
    [
        (CollaborationStatus.PROPOSED, CollaborationStatus.ACCEPTED),
        (CollaborationStatus.PROPOSED, CollaborationStatus.DECLINED),
        (CollaborationStatus.PROPOSED, CollaborationStatus.CANCELLED),
        (CollaborationStatus.ACCEPTED, CollaborationStatus.IN_PROGRESS),
        (CollaborationStatus.ACCEPTED, CollaborationStatus.COMPLETED),
        (CollaborationStatus.ACCEPTED, CollaborationStatus.CANCELLED),
        (CollaborationStatus.IN_PROGRESS, CollaborationStatus.COMPLETED),
        (CollaborationStatus.IN_PROGRESS, CollaborationStatus.CANCELLED),
    ],
)
def test_valid_status_transitions_are_applied(current_status, next_status):
    collaboration = make_collaboration(current_status)

    collaboration._update_collaboration(next_status)

    assert collaboration.status == next_status


@pytest.mark.parametrize(
    "terminal_status",
    [
        CollaborationStatus.DECLINED,
        CollaborationStatus.COMPLETED,
        CollaborationStatus.CANCELLED,
    ],
)
def test_terminal_statuses_reject_all_transitions(terminal_status):
    collaboration = make_collaboration(terminal_status)

    with pytest.raises(ValueError, match="Invalid status transition"):
        collaboration._update_collaboration(CollaborationStatus.ACCEPTED)


def test_invalid_status_value_is_rejected():
    collaboration = make_collaboration()

    with pytest.raises(ValueError, match="Invalid status"):
        collaboration._update_collaboration("not-a-status")
