"""Focused tests for the collaboration-to-messaging handoff."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.messaging import Conversation
from services.collaboration_messaging_service import CollaborationMessagingService


class FakeConversationRepository:
    def __init__(self):
        self.conversation = None
        self.created = []
        self.participants = set()
        self.blocked = False
        self.active_user_ids = None

    async def get_active_user_ids(self, user_ids):
        return user_ids if self.active_user_ids is None else self.active_user_ids

    async def users_are_blocked(self, user_id, participant_ids):
        return self.blocked

    async def get_direct_conversation(self, user_id, participant_id):
        return self.conversation

    async def create(self, conversation):
        conversation.id = uuid.uuid4()
        self.created.append(conversation)
        self.conversation = conversation
        return conversation

    async def get_participant(self, conversation_id, user_id):
        return user_id if (conversation_id, user_id) in self.participants else None

    async def add_participant(self, participant):
        self.participants.add((participant.conversation_id, participant.user_id))
        return participant


def collaboration_pair():
    initiator_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    return (
        SimpleNamespace(initiator_id=initiator_id),
        SimpleNamespace(user_id=participant_id),
    )


@pytest.mark.asyncio
async def test_accepted_collaboration_creates_direct_conversation_and_members():
    repository = FakeConversationRepository()
    collaboration, participant = collaboration_pair()

    conversation = await CollaborationMessagingService(repository).ensure_direct_conversation(
        collaboration, participant
    )

    assert conversation.is_group is False
    assert len(repository.created) == 1
    assert repository.participants == {
        (conversation.id, collaboration.initiator_id),
        (conversation.id, participant.user_id),
    }


@pytest.mark.asyncio
async def test_existing_direct_conversation_is_reused_idempotently():
    repository = FakeConversationRepository()
    collaboration, participant = collaboration_pair()
    existing = Conversation(id=uuid.uuid4(), is_group=False)
    repository.conversation = existing

    first = await CollaborationMessagingService(repository).ensure_direct_conversation(
        collaboration, participant
    )
    second = await CollaborationMessagingService(repository).ensure_direct_conversation(
        collaboration, participant
    )

    assert first is existing
    assert second is existing
    assert repository.created == []
    assert repository.participants == {
        (existing.id, collaboration.initiator_id),
        (existing.id, participant.user_id),
    }


@pytest.mark.asyncio
async def test_blocked_collaborators_cannot_receive_messaging_bridge():
    repository = FakeConversationRepository()
    repository.blocked = True
    collaboration, participant = collaboration_pair()

    with pytest.raises(PermissionError, match="blocked"):
        await CollaborationMessagingService(repository).ensure_direct_conversation(
            collaboration, participant
        )

    assert repository.created == []
    assert repository.participants == set()