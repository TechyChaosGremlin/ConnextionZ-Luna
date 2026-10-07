"""Issue and consume server-authoritative Paid interaction contexts."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paid_delivery import PaidInteractionContext
from repositories.paid_delivery_repository import PaidDeliveryRepository

CONTEXT_TTL = timedelta(minutes=5)
MAX_CONTEXT_LENGTH = 128


class PaidInteractionContextService:
    """Bind a single Paid-attributed interaction to a recent Paid selection."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.delivery_repository = PaidDeliveryRepository(db)

    async def issue(self, viewer_id: uuid.UUID, delivery_id: uuid.UUID) -> str:
        self.delivery_repository.require_postgresql()
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()

        async with self.db.begin_nested():
            selection = await self.delivery_repository.get_selection(delivery_id, viewer_id)
            now = await self.db.scalar(select(func.clock_timestamp()))
            if now is None:
                raise RuntimeError("Database time is unavailable for Paid interaction context")
            self.db.add(
                PaidInteractionContext(
                    delivery_id=selection.id,
                    viewer_id=selection.viewer_id,
                    post_id=selection.post_id,
                    token_hash=token_hash,
                    expires_at=now + CONTEXT_TTL,
                )
            )
            await self.db.flush()
        return token

    async def consume(
        self,
        token: str,
        *,
        viewer_id: uuid.UUID,
        post_id: uuid.UUID,
        delivery_id: uuid.UUID,
    ) -> None:
        self.delivery_repository.require_postgresql()
        if not isinstance(token, str) or not 1 <= len(token) <= MAX_CONTEXT_LENGTH:
            raise ValueError("Paid interaction context is invalid")
        try:
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError:
            raise ValueError("Paid interaction context is invalid") from None

        async with self.db.begin_nested():
            await self.delivery_repository.impressed_for_engagement(delivery_id, viewer_id, post_id)
            context = (
                await self.db.execute(
                    select(PaidInteractionContext)
                    .where(
                        PaidInteractionContext.delivery_id == delivery_id,
                        PaidInteractionContext.viewer_id == viewer_id,
                        PaidInteractionContext.post_id == post_id,
                        PaidInteractionContext.token_hash == token_hash,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            now = await self.db.scalar(select(func.clock_timestamp()))
            if (
                now is None
                or context is None
                or context.consumed_at is not None
                or context.expires_at <= now
            ):
                raise ValueError("Paid interaction context is invalid or expired")
            context.consumed_at = now
            await self.db.flush()
