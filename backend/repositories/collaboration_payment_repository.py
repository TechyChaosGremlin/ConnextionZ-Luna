"""Database access for collaboration payments."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collaboration import Collaboration, CollaborationParticipant
from app.models.collaboration_payment import CollaborationPayment


class CollaborationPaymentRepository:
    """Persist and retrieve payment rows without committing caller transactions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_collaboration(
        self, collaboration_id: uuid.UUID, *, for_update: bool = False
    ) -> Collaboration | None:
        statement = select(Collaboration).where(
            Collaboration.id == collaboration_id,
            Collaboration.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_participant_user_ids(
        self, collaboration_id: uuid.UUID
    ) -> set[uuid.UUID]:
        result = await self.db.execute(
            select(CollaborationParticipant.user_id).where(
                CollaborationParticipant.collaboration_id == collaboration_id
            )
        )
        return set(result.scalars().all())

    async def get_accepted_participant_user_ids(
        self, collaboration_id: uuid.UUID
    ) -> set[uuid.UUID]:
        result = await self.db.execute(
            select(CollaborationParticipant.user_id).where(
                CollaborationParticipant.collaboration_id == collaboration_id,
                CollaborationParticipant.accepted.is_(True),
            )
        )
        return set(result.scalars().all())

    async def get_for_collaboration(
        self, collaboration_id: uuid.UUID, *, for_update: bool = False
    ) -> CollaborationPayment | None:
        statement = select(CollaborationPayment).where(
            CollaborationPayment.collaboration_id == collaboration_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_hold_idempotency_key(
        self, idempotency_key: str, *, for_update: bool = False
    ) -> CollaborationPayment | None:
        statement = select(CollaborationPayment).where(
            CollaborationPayment.hold_idempotency_key == idempotency_key
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_provider_reference(
        self, provider_name: str, provider_reference: str
    ) -> CollaborationPayment | None:
        result = await self.db.execute(
            select(CollaborationPayment).where(
                CollaborationPayment.payment_provider == provider_name,
                CollaborationPayment.provider_reference == provider_reference,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, payment: CollaborationPayment) -> CollaborationPayment:
        self.db.add(payment)
        await self.db.flush()
        return payment

    async def save(self, payment: CollaborationPayment) -> CollaborationPayment:
        await self.db.flush()
        return payment
