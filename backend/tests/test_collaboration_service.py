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


def make_collaboration(status=CollaborationStatus.PROPOSED):
    return Collaboration(
        initiator_id=uuid.uuid4(),
        title="Test collaboration",
        status=status,
    )


def make_participant(accepted=False):
    return CollaborationParticipant(
        id=uuid.uuid4(),
        accepted=accepted,
        accepted_at=None,
    )


def make_service():
    mock_repository = AsyncMock(spec=CollaborationRepository)
    repository = cast(CollaborationRepository, mock_repository)
    return CollaborationService(repository), mock_repository


@pytest.mark.asyncio
async def test_accept_participant():
    collaboration = make_collaboration()
    participant = make_participant()
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
    participant = make_participant()
    service, repository = make_service()

    result = await service.decline_participant(collaboration, participant)

    assert result is True
    assert collaboration.status == CollaborationStatus.DECLINED
    repository.remove_participant.assert_awaited_once_with(participant)
    repository.update.assert_awaited_once_with(collaboration)


@pytest.mark.asyncio
async def test_update_status_requires_accepted_before_in_progress():
    collaboration = make_collaboration()
    service, repository = make_service()

    with pytest.raises(ValueError, match="non-ACCEPTED"):
        await service.update_status(collaboration, CollaborationStatus.IN_PROGRESS)

    repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_status_moves_accepted_to_in_progress():
    collaboration = make_collaboration(CollaborationStatus.ACCEPTED)
    service, repository = make_service()

    result = await service.update_status(collaboration, CollaborationStatus.IN_PROGRESS)

    assert result is collaboration
    assert collaboration.status == CollaborationStatus.IN_PROGRESS
    repository.update.assert_awaited_once_with(collaboration)