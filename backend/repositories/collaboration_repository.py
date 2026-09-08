"""
Collaboration repository for database operations.

Provides CRUD operations for Collaboration, CollaborationParticipant, and Milestone models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.collaboration import (
    Collaboration, CollaborationParticipant, Milestone,
    CollaborationStatus, MilestoneStatus,
)
from repositories.base import BaseRepository


class CollaborationRepository(BaseRepository[Collaboration]):
    """Repository for Collaboration model database operations.

    Extends BaseRepository with common CRUD operations and adds
    collaboration-specific methods for marketplace, participants, and milestones.
    """

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        super().__init__(db, Collaboration)

    async def get_by_id_for_update(self, entity_id: uuid.UUID) -> Optional[Collaboration]:
        """Get a collaboration and lock its row for the transaction.

        Serializes concurrent Accept/Decline calls on the same collaboration
        so a duplicate request can't slip past the pending-state check before
        the first request commits.
        """
        result = await self.db.execute(
            select(Collaboration).where(Collaboration.id == entity_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_for_user(
        self,
        user_id: uuid.UUID,
        status: Optional[CollaborationStatus] = None,
        limit: int = 20,
        before: Optional[tuple[datetime, uuid.UUID]] = None,
    ) -> list[Collaboration]:
        """Get collaborations where user is initiator or participant."""
        # Subquery to find collaboration IDs where user is a participant
        participant_collab_ids = select(CollaborationParticipant.collaboration_id).where(
            CollaborationParticipant.user_id == user_id
        ).subquery()

        stmt = select(Collaboration).where(
            or_(
                Collaboration.initiator_id == user_id,
                Collaboration.id.in_(participant_collab_ids),
            )
        )
        if status:
            stmt = stmt.where(Collaboration.status == status)
        if before:
            before_time, before_id = before
            stmt = stmt.where(
                or_(
                    Collaboration.created_at < before_time,
                    and_(
                        Collaboration.created_at == before_time,
                        Collaboration.id < before_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            Collaboration.created_at.desc(), Collaboration.id.desc()
        ).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def status_counts_for_user(self, user_id: uuid.UUID) -> dict[CollaborationStatus, int]:
        """Count collaborations for a user grouped by status."""
        participant_collab_ids = select(CollaborationParticipant.collaboration_id).where(
            CollaborationParticipant.user_id == user_id
        ).subquery()

        result = await self.db.execute(
            select(Collaboration.status, func.count().label("count"))
            .where(
                Collaboration.deleted_at.is_(None),
                or_(
                    Collaboration.initiator_id == user_id,
                    Collaboration.id.in_(participant_collab_ids),
                ),
            )
            .group_by(Collaboration.status)
        )
        return {row.status: int(row.count or 0) for row in result.all()}

    async def get_for_user_in_period(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[Collaboration]:
        """Return a user's non-deleted collaborations created in a period."""
        participant_collab_ids = select(CollaborationParticipant.collaboration_id).where(
            CollaborationParticipant.user_id == user_id
        ).subquery()
        result = await self.db.execute(
            select(Collaboration)
            .options(selectinload(Collaboration.participants))
            .where(
                Collaboration.deleted_at.is_(None),
                Collaboration.created_at >= start,
                Collaboration.created_at <= end,
                or_(
                    Collaboration.initiator_id == user_id,
                    Collaboration.id.in_(participant_collab_ids),
                ),
            )
            .order_by(Collaboration.created_at.asc(), Collaboration.id.asc())
        )
        return list(result.scalars().all())

    async def get_marketplace(
        self,
        tags: Optional[list[str]] = None,
        content_type: Optional[str] = None,
        limit: int = 20,
        before: Optional[tuple[datetime, uuid.UUID]] = None,
    ) -> list[Collaboration]:
        """Get public collaboration marketplace listings."""
        stmt = select(Collaboration).where(
            Collaboration.status == CollaborationStatus.PROPOSED
        )
        if content_type:
            stmt = stmt.where(Collaboration.content_type == content_type)
        if tags:
            # Filter by tags (JSONB contains any of the provided tags)
            for tag in tags:
                stmt = stmt.where(Collaboration.tags.contains([tag]))
        if before:
            before_time, before_id = before
            stmt = stmt.where(
                or_(
                    Collaboration.created_at < before_time,
                    and_(
                        Collaboration.created_at == before_time,
                        Collaboration.id < before_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            Collaboration.created_at.desc(), Collaboration.id.desc()
        ).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # --- Participant Methods ---

    async def add_participant(
        self, participant: CollaborationParticipant
    ) -> CollaborationParticipant:
        """Add a participant to a collaboration."""
        self.db.add(participant)
        await self.db.flush()
        await self.db.refresh(participant)
        return participant

    async def remove_participant(
        self, participant: CollaborationParticipant
    ) -> None:
        """Remove a participant from a collaboration."""
        await self.db.delete(participant)
        await self.db.flush()

    async def get_participant(
        self, collab_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[CollaborationParticipant]:
        """Get a specific participant in a collaboration."""
        result = await self.db.execute(
            select(CollaborationParticipant).where(
                CollaborationParticipant.collaboration_id == collab_id,
                CollaborationParticipant.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_participant(
        self, participant: CollaborationParticipant
    ) -> CollaborationParticipant:
        """Update a collaboration participant."""
        await self.db.flush()
        await self.db.refresh(participant)
        return participant

    # --- Milestone Methods ---

    async def add_milestone(self, milestone: Milestone) -> Milestone:
        """Add a milestone to a collaboration."""
        self.db.add(milestone)
        await self.db.flush()
        await self.db.refresh(milestone)
        return milestone

    async def get_milestones(self, collab_id: uuid.UUID) -> list[Milestone]:
        """Get all milestones for a collaboration."""
        result = await self.db.execute(
            select(Milestone)
            .where(Milestone.collaboration_id == collab_id)
            .order_by(Milestone.sort_order)
        )
        return list(result.scalars().all())

    async def get_milestone_by_id(self, milestone_id: uuid.UUID) -> Optional[Milestone]:
        """Get a single milestone by its id."""
        result = await self.db.execute(
            select(Milestone).where(Milestone.id == milestone_id)
        )
        return result.scalar_one_or_none()

    async def update_milestone(self, milestone: Milestone) -> Milestone:
        """Update a milestone."""
        await self.db.flush()
        await self.db.refresh(milestone)
        return milestone
