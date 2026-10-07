"""PostgreSQL-locked, transactional Paid impression and reach accounting."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paid_campaign import PaidCampaign
from app.models.paid_delivery import PaidDelivery


class PaidDeliveryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def require_postgresql(self) -> None:
        if self.db.get_bind().dialect.name != "postgresql":
            raise NotImplementedError("Atomic Paid delivery accounting requires PostgreSQL")

    async def lock_campaign(self, campaign_id: uuid.UUID) -> PaidCampaign:
        self.require_postgresql()
        campaign = (
            await self.db.execute(
                select(PaidCampaign)
                .where(PaidCampaign.id == campaign_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if campaign is None:
            raise ValueError("Paid campaign not found")
        return campaign

    async def server_time(self) -> datetime:
        return (await self.db.execute(select(func.clock_timestamp()))).scalar_one()

    async def get_selection(self, delivery_id: uuid.UUID, viewer_id: uuid.UUID) -> PaidDelivery:
        selection = (
            await self.db.execute(
                select(PaidDelivery).where(
                    PaidDelivery.id == delivery_id,
                    PaidDelivery.viewer_id == viewer_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if selection is None:
            raise ValueError("Paid delivery selection not found")
        return selection

    async def latest_impressed_for_engagement(
        self, viewer_id: uuid.UUID, post_id: uuid.UUID
    ) -> PaidDelivery | None:
        """Resolve provenance only from a delivered server selection."""
        selection = (
            await self.db.execute(
                select(PaidDelivery)
                .where(
                    PaidDelivery.viewer_id == viewer_id,
                    PaidDelivery.post_id == post_id,
                    PaidDelivery.impressed_at.is_not(None),
                )
                .order_by(PaidDelivery.impressed_at.desc(), PaidDelivery.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if selection is None:
            return None
        if (
            selection.viewer_id != viewer_id
            or selection.post_id != post_id
            or selection.impressed_at is None
            or not isinstance(selection.id, uuid.UUID)
            or not isinstance(selection.campaign_id, uuid.UUID)
        ):
            raise ValueError("Paid engagement delivery provenance is invalid")
        return selection

    async def impressed_for_engagement(
        self, delivery_id: uuid.UUID, viewer_id: uuid.UUID, post_id: uuid.UUID
    ) -> PaidDelivery:
        selection = (
            await self.db.execute(
                select(PaidDelivery).where(
                    PaidDelivery.id == delivery_id,
                    PaidDelivery.viewer_id == viewer_id,
                    PaidDelivery.post_id == post_id,
                    PaidDelivery.impressed_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if selection is None:
            raise ValueError("Paid engagement delivery provenance is invalid")
        if (
            not isinstance(selection.id, uuid.UUID)
            or not isinstance(selection.campaign_id, uuid.UUID)
            or selection.viewer_id != viewer_id
            or selection.post_id != post_id
            or selection.impressed_at is None
        ):
            raise ValueError("Paid engagement delivery provenance is invalid")
        return selection

    async def create_selection(
        self, campaign: PaidCampaign, viewer_id: uuid.UUID, now: datetime
    ) -> PaidDelivery:
        selection = PaidDelivery(
            campaign_id=campaign.id,
            viewer_id=viewer_id,
            post_id=campaign.post_id,
            selected_at=now,
        )
        self.db.add(selection)
        await self.db.flush()
        return selection

    async def record_locked(
        self, campaign: PaidCampaign, selection: PaidDelivery, now: datetime
    ) -> PaidDelivery:
        """Caller must hold the campaign lock and have revalidated all gates."""
        previously_reached = (
            await self.db.execute(
                select(
                    exists().where(
                        PaidDelivery.campaign_id == campaign.id,
                        PaidDelivery.viewer_id == selection.viewer_id,
                        PaidDelivery.impressed_at.is_not(None),
                    )
                )
            )
        ).scalar_one()
        await self.db.execute(
            update(PaidCampaign)
            .where(PaidCampaign.id == campaign.id)
            .values(
                impressions_delivered=PaidCampaign.impressions_delivered + 1,
                reach_delivered=PaidCampaign.reach_delivered + (0 if previously_reached else 1),
            )
        )
        selection.impressed_at = now
        await self.db.flush()
        return selection
