"""
Messaging repository for database operations.

Provides CRUD operations for Conversation and Message models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import DateTime, and_, case, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.messaging import (
    Conversation, ConversationParticipant, Message,
)
from app.models.social import UserBlock
from app.models.user import AccountStatus, User
from repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    """Repository for Conversation model database operations.

    Extends BaseRepository with common CRUD operations and adds
    conversation-specific methods for participants and user lookups.
    """

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        super().__init__(db, Conversation)

    async def get_for_user(
        self,
        user_id: uuid.UUID,
        limit: int = 20,
        before: Optional[tuple[str, uuid.UUID]] = None,
    ) -> List[Conversation]:
        """Get conversations where user is a participant."""
        # Subquery to find conversation IDs where user is a participant
        participant_conv_ids = select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == user_id
        ).subquery()

        stmt = select(Conversation).where(
            Conversation.id.in_(participant_conv_ids),
            Conversation.deleted_at.is_(None),
        )
        if before:
            before_time, before_id = before
            stmt = stmt.where(
                or_(
                    Conversation.last_message_at < before_time,
                    and_(
                        Conversation.last_message_at == before_time,
                        Conversation.id < before_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            Conversation.last_message_at.desc().nullslast(), Conversation.id.desc()
        ).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_direct_conversation(
        self, user_id: uuid.UUID, participant_id: uuid.UUID
    ) -> Optional[Conversation]:
        """Return the existing one-to-one conversation for two users."""
        matching_conversations = (
            select(ConversationParticipant.conversation_id)
            .group_by(ConversationParticipant.conversation_id)
            .having(func.count(ConversationParticipant.user_id) == 2)
            .having(
                func.sum(
                    case(
                        (ConversationParticipant.user_id.in_((user_id, participant_id)), 1),
                        else_=0,
                    )
                ) == 2
            )
            .subquery()
        )
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id.in_(matching_conversations.select()),
                Conversation.is_group.is_(False),
                Conversation.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """Count non-deleted conversations visible to a user."""
        participant_conv_ids = select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == user_id
        )
        result = await self.db.execute(
            select(func.count(Conversation.id)).where(
                Conversation.id.in_(participant_conv_ids),
                Conversation.deleted_at.is_(None),
            )
        )
        return result.scalar_one()

    async def users_are_blocked(
        self, user_id: uuid.UUID, participant_ids: list[uuid.UUID]
    ) -> bool:
        """Return whether either party has blocked the other."""
        if not participant_ids:
            return False
        blocked = exists().where(
            (UserBlock.blocker_id == user_id) & UserBlock.blocked_id.in_(participant_ids)
            | (UserBlock.blocked_id == user_id) & UserBlock.blocker_id.in_(participant_ids)
        )
        return bool(await self.db.scalar(select(blocked)))

    async def add_participant(
        self, participant: ConversationParticipant
    ) -> ConversationParticipant:
        """Add a participant to a conversation."""
        self.db.add(participant)
        await self.db.flush()
        await self.db.refresh(participant)
        return participant

    async def get_participant(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[ConversationParticipant]:
        """Get a specific participant in a conversation."""
        result = await self.db.execute(
            select(ConversationParticipant)
            .join(Conversation)
            .where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_participant_ids(self, conversation_id: uuid.UUID) -> List[uuid.UUID]:
        """Return member IDs for a conversation."""
        result = await self.db.execute(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == conversation_id
            )
        )
        return list(result.scalars().all())

    async def get_active_user_ids(self, user_ids: list[uuid.UUID]) -> List[uuid.UUID]:
        """Return requested user IDs that can participate in messaging."""
        if not user_ids:
            return []
        result = await self.db.execute(
            select(User.id).where(
                User.id.in_(user_ids),
                User.status == AccountStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_notification_recipient_ids(
        self, conversation_id: uuid.UUID, sender_id: uuid.UUID
    ) -> List[uuid.UUID]:
        """Return unmuted participants other than the sender."""
        result = await self.db.execute(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id != sender_id,
                ConversationParticipant.is_muted.is_(False),
            )
        )
        return list(result.scalars().all())


class MessageRepository(BaseRepository[Message]):
    """Repository for Message model database operations.

    Extends BaseRepository with common CRUD operations and adds
    message-specific methods for conversation and status management.
    """

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        super().__init__(db, Message)

    async def get_for_conversation(
        self,
        conversation_id: uuid.UUID,
        limit: int = 50,
        before: Optional[tuple[datetime, uuid.UUID]] = None,
    ) -> List[Message]:
        """Get messages for a conversation."""
        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.deleted_at.is_(None),
        )
        if before:
            before_time, before_id = before
            stmt = stmt.where(
                or_(
                    Message.created_at < before_time,
                    and_(Message.created_at == before_time, Message.id < before_id),
                )
            )
        stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_client_message_id(
        self, conversation_id: uuid.UUID, sender_id: uuid.UUID, client_message_id: str
    ) -> Optional[Message]:
        result = await self.db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.sender_id == sender_id,
                Message.client_message_id == client_message_id,
            )
        )
        return result.scalar_one_or_none()

    async def count_for_conversation(self, conversation_id: uuid.UUID) -> int:
        """Count non-deleted messages in a conversation."""
        result = await self.db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id,
                Message.deleted_at.is_(None),
            )
        )
        return result.scalar_one()

    async def mark_conversation_read(
        self, participant: ConversationParticipant
    ) -> ConversationParticipant:
        participant.last_read_at = datetime.now(timezone.utc).isoformat()
        await self.db.flush()
        await self.db.refresh(participant)
        return participant

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count(Message.id))
            .join(
                ConversationParticipant,
                and_(
                    ConversationParticipant.conversation_id == Message.conversation_id,
                    ConversationParticipant.user_id == user_id,
                ),
            )
            .where(
                Message.sender_id != user_id,
                Message.deleted_at.is_(None),
                (ConversationParticipant.last_read_at.is_(None))
                | (
                    Message.created_at
                    > cast(ConversationParticipant.last_read_at, DateTime(timezone=True))
                ),
            )
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def soft_delete(self, message: Message) -> None:
        message.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
