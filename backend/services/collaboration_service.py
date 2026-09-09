"""Collaboration workflow services."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.collaboration import Collaboration, CollaborationParticipant, CollaborationStatus
from repositories.collaboration_repository import CollaborationRepository


class CollaborationService:
    """Apply collaboration lifecycle rules through the repository."""

    def __init__(self, repository: CollaborationRepository):
        self.repository = repository

    async def accept_participant(
        self,
        collaboration: Collaboration,
        participant: CollaborationParticipant,
    ) -> CollaborationParticipant:
        if participant.accepted:
            raise ValueError("Collaboration invitation has already been accepted")
        if collaboration.status != CollaborationStatus.PROPOSED:
            raise ValueError("Collaboration invitation is no longer pending")

        participant.accepted = True
        participant.accepted_at = datetime.now(timezone.utc).isoformat()
        await self.repository.update_participant(participant)
        collaboration.status = CollaborationStatus.ACCEPTED
        await self.repository.update(collaboration)
        return participant

    async def decline_participant(
        self,
        collaboration: Collaboration,
        participant: CollaborationParticipant,
    ) -> bool:
        if participant.accepted:
            raise ValueError("An accepted collaboration cannot be declined")
        if collaboration.status != CollaborationStatus.PROPOSED:
            raise ValueError("Collaboration invitation is no longer pending")

        await self.repository.remove_participant(participant)
        collaboration.status = CollaborationStatus.DECLINED
        await self.repository.update(collaboration)
        return True

    async def update_status(
        self,
        collaboration: Collaboration,
        new_status: CollaborationStatus | str,
    ) -> Collaboration:
        collaboration._update_collaboration(new_status)
        await self.repository.update(collaboration)
        return collaboration
