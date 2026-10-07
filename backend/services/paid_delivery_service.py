"""Internal-only Paid selection/accounting, not a public delivery endpoint."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import lazyload

from app.models.paid_campaign import PaidCampaign
from app.models.user import Profile, User
from repositories.paid_campaign_repository import PaidCampaignRepository
from repositories.paid_delivery_repository import PaidDeliveryRepository
from repositories.paid_impression_history_repository import PaidImpressionHistoryRepository
from repositories.social_repository import FeedSafetyRepository
from services.paid_campaign_service import PaidCampaignService, PaidCandidate


@dataclass(frozen=True)
class PaidImpressionReceipt:
    delivery_id: uuid.UUID
    post_id: uuid.UUID
    paid_campaign_id: uuid.UUID
    source: Literal["PAID"] = field(default="PAID", init=False)
    is_sponsored: bool = field(default=True, init=False)
    sponsored_label: str = field(default="Sponsored", init=False)


class PaidDeliveryService:
    """Create server selections separately from explicitly recorded impressions."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = PaidDeliveryRepository(db)

    async def _viewer(self, viewer: User) -> User:
        stored = (
            await self.db.execute(
                select(User)
                .options(lazyload("*"))
                .where(User.id == viewer.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if stored is None:
            raise PermissionError("Viewer not found")
        PaidCampaignService.require_active_user(stored)
        return stored

    async def _validate(self, campaign: PaidCampaign, viewer: User, now: datetime) -> None:
        eligible = await PaidCampaignRepository(self.db).get_eligible(
            now, limit=1, campaign_id=campaign.id
        )
        hidden = await FeedSafetyRepository(self.db).get_hidden_creator_ids(
            viewer.id, [campaign.owner_id]
        )
        if not eligible or campaign.owner_id in hidden:
            raise ValueError("Campaign or promoted content is not eligible for Paid delivery")
        # Verify lifetime completeness even for uncapped campaigns before accounting.
        await PaidImpressionHistoryRepository(self.db).count_paid_impressions(
            viewer_id=viewer.id,
            campaign_id=campaign.id,
            start_at=campaign.start_at,
            end_at=now,
        )
        if campaign.targeting:
            # The shared evaluator must not reuse a pre-lock cached targeting profile.
            await self.db.scalar(
                select(Profile)
                .where(Profile.user_id == viewer.id)
                .execution_options(populate_existing=True)
            )
        evaluation = await PaidCampaignService(self.db).evaluate_eligibility(
            campaign, viewer, now=now
        )
        if not evaluation.eligible:
            raise ValueError("Viewer targeting or frequency is not eligible for Paid delivery")

    async def select_candidate(self, viewer: User, candidate: PaidCandidate) -> uuid.UUID:
        """Persist a server selection ID without counting an impression."""
        self.repository.require_postgresql()
        async with self.db.begin_nested():
            campaign = await self.repository.lock_campaign(candidate.paid_campaign_id)
            viewer = await self._viewer(viewer)
            if candidate.post_id != campaign.post_id:
                raise ValueError("Promoted post does not match the campaign")
            now = await self.repository.server_time()
            await self._validate(campaign, viewer, now)
            selection = await self.repository.create_selection(campaign, viewer.id, now)
            return selection.id

    async def record_impression(
        self, viewer: User, delivery_id: uuid.UUID
    ) -> PaidImpressionReceipt:
        """Consume a persisted selection once; caller commits the encompassing transaction."""
        self.repository.require_postgresql()
        async with self.db.begin_nested():
            selection = await self.repository.get_selection(delivery_id, viewer.id)
            campaign = await self.repository.lock_campaign(selection.campaign_id)
            # Another request may have recorded the selection while we waited for the lock.
            selection = await self.repository.get_selection(delivery_id, viewer.id)
            if selection.post_id != campaign.post_id:
                raise ValueError("Promoted post does not match the campaign")
            viewer = await self._viewer(viewer)
            if selection.impressed_at is None:
                now = await self.repository.server_time()
                await self._validate(campaign, viewer, now)
                await self.repository.record_locked(campaign, selection, now)
            return PaidImpressionReceipt(
                delivery_id=selection.id,
                post_id=selection.post_id,
                paid_campaign_id=selection.campaign_id,
            )
