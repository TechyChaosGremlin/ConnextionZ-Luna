"""
Reputation repository for database operations.

Provides CRUD operations for ReputationScore, Endorsement, Badge, and UserBadge models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reputation import (
    Badge,
    Endorsement,
    ReputationScore,
    UserBadge,
)


class ReputationRepository:
    """Repository for reputation-related database operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_endorsement(
        self,
        endorsement: Endorsement,
    ) -> Endorsement:
        """Create a new endorsement."""
        self.db.add(endorsement)
        await self.db.flush()
        await self.db.refresh(endorsement)
        return endorsement

    async def get_endorsement_by_id(
        self,
        endorsement_id: uuid.UUID,
    ) -> Optional[Endorsement]:
        """Get an endorsement by ID."""
        result = await self.db.execute(
            select(Endorsement).where(
                Endorsement.id == endorsement_id
            )
        )
        return result.scalar_one_or_none()

    async def get_endorsements_for_user(
        self,
        user_id: uuid.UUID,
        limit: int = 20,
    ) -> List[Endorsement]:
        """Get endorsements received by a user."""
        stmt = (
            select(Endorsement)
            .where(Endorsement.endorsee_id == user_id)
            .order_by(Endorsement.created_at.desc())
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_reputation_score(
        self,
        user_id: uuid.UUID,
    ) -> Optional[ReputationScore]:
        """Get the reputation score for a user."""
        result = await self.db.execute(
            select(ReputationScore).where(
                ReputationScore.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def create_or_update_reputation_score(
        self,
        user_id: uuid.UUID,
    ) -> ReputationScore:
        """Recalculate and update the reputation score for a user."""
        score = await self.get_reputation_score(user_id)

        if not score:
            score = ReputationScore(user_id=user_id)
            self.db.add(score)

        endorsement_count = await self._count_endorsements(user_id)

        score.total_endorsements = endorsement_count
        score.overall_score = min(100, endorsement_count * 10)
        score.computed_at = datetime.now(timezone.utc).isoformat()

        await self.db.flush()
        await self.db.refresh(score)

        return score

    async def _count_endorsements(
        self,
        user_id: uuid.UUID,
    ) -> int:
        """Count endorsements received by a user."""
        stmt = select(func.count(Endorsement.id)).where(
            Endorsement.endorsee_id == user_id
        )

        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def get_available_badges(self) -> List[Badge]:
        """Get all available badge definitions."""
        result = await self.db.execute(
            select(Badge).order_by(Badge.name)
        )
        return list(result.scalars().all())

    async def get_user_badges(
        self,
        user_id: uuid.UUID,
    ) -> List[Badge]:
        """Get badges earned by a user."""
        stmt = (
            select(Badge)
            .join(UserBadge, UserBadge.badge_id == Badge.id)
            .where(UserBadge.user_id == user_id)
            .order_by(UserBadge.awarded_at.desc())
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())