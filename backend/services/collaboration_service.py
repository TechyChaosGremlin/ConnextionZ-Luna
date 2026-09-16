"""Collaboration workflow services."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collaboration import Collaboration, CollaborationParticipant, CollaborationStatus
from app.models.user import AccountStatus, User
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


class CollaborationInviteEligibilityService:
    """Validate collaboration invitees before participant rows are created."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def validate_participant_ids(
        self, initiator: User, participant_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("Duplicate collaboration participant IDs are not allowed")
        if initiator.id in participant_ids:
            raise ValueError("You cannot invite yourself to a collaboration")

        from repositories.profile_repository import ProfileRepository
        from repositories.social_repository import FeedSafetyRepository, FollowRepository
        from repositories.user_repository import UserRepository

        user_repository = UserRepository(self.db)
        profile_repository = ProfileRepository(self.db)
        follow_repository = FollowRepository(self.db)
        restricted_ids = await FeedSafetyRepository(
            self.db
        ).get_invitation_restricted_user_ids(initiator.id, participant_ids)

        for participant_id in participant_ids:
            target = await user_repository.get_by_id(participant_id)
            if target is None or getattr(target, "deleted_at", None) is not None:
                raise ValueError("Creator not found")
            if target.status != AccountStatus.ACTIVE:
                raise ValueError("Creator account is not active")
            if participant_id in restricted_ids:
                raise PermissionError("Creator cannot be invited due to a block or mute")

            profile = await profile_repository.get_by_user_id(participant_id)
            if profile is None or getattr(profile, "deleted_at", None) is not None:
                continue
            if not profile.open_to_collab:
                raise PermissionError("Creator is not open to collaborations")
            if profile.private_account and not await follow_repository.is_following(
                initiator.id, participant_id
            ):
                raise PermissionError("Creator only accepts collaboration invites from followers")

        return participant_ids
