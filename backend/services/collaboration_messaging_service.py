"""Create the direct messaging bridge for an accepted collaboration."""

from __future__ import annotations

from app.models.collaboration import Collaboration, CollaborationParticipant
from app.models.messaging import Conversation, ConversationParticipant
from repositories.messaging_repository import ConversationRepository


class CollaborationMessagingService:
    """Ensure collaborators have a direct messaging conversation."""

    def __init__(self, repository: ConversationRepository):
        self.repository = repository

    async def ensure_direct_conversation(
        self,
        collaboration: Collaboration,
        accepted_participant: CollaborationParticipant,
    ) -> Conversation:
        initiator_id = collaboration.initiator_id
        participant_id = accepted_participant.user_id

        active_user_ids = await self.repository.get_active_user_ids(
            [initiator_id, participant_id]
        )
        if len(active_user_ids) != 2:
            raise ValueError("One or more collaboration participants are unavailable")
        if await self.repository.users_are_blocked(initiator_id, [participant_id]):
            raise PermissionError("Cannot message a blocked user")

        conversation = await self.repository.get_direct_conversation(
            initiator_id, participant_id
        )
        if conversation is None:
            conversation = await self.repository.create(Conversation(is_group=False))

        for user_id in (initiator_id, participant_id):
            if not await self.repository.get_participant(conversation.id, user_id):
                await self.repository.add_participant(
                    ConversationParticipant(
                        conversation_id=conversation.id,
                        user_id=user_id,
                    )
                )
        return conversation