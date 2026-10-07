"""Persistence and bounded candidate queries, separate from normal feed pools."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentStatus, Post
from app.models.paid_campaign import PaidCampaign, PaidCampaignStatus
from app.models.user import AccountStatus, Profile, User
from repositories.base import BaseRepository


class PaidCampaignRepository(BaseRepository[PaidCampaign]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, PaidCampaign)

    async def get_owned(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID, *, for_update: bool = False
    ) -> PaidCampaign | None:
        stmt = select(PaidCampaign).where(
            PaidCampaign.id == campaign_id, PaidCampaign.owner_id == owner_id
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_eligible(
        self,
        now: datetime,
        *,
        limit: int = 100,
        campaign_id: uuid.UUID | None = None,
        campaign_ids: tuple[uuid.UUID, ...] | None = None,
    ) -> list[PaidCampaign]:
        if not 1 <= limit <= 100:
            raise ValueError("Paid candidate limit must be between 1 and 100")
        stmt = (
            select(PaidCampaign)
            .join(Post, Post.id == PaidCampaign.post_id)
            .join(User, User.id == PaidCampaign.owner_id)
            .outerjoin(Profile, Profile.user_id == User.id)
            .where(
                PaidCampaign.status == PaidCampaignStatus.ACTIVE,
                PaidCampaign.start_at <= now,
                PaidCampaign.end_at > now,
                PaidCampaign.spent_minor_units < PaidCampaign.budget_minor_units,
                or_(
                    PaidCampaign.max_impressions.is_(None),
                    PaidCampaign.impressions_delivered < PaidCampaign.max_impressions,
                ),
                or_(
                    PaidCampaign.max_reach.is_(None),
                    PaidCampaign.reach_delivered < PaidCampaign.max_reach,
                ),
                Post.user_id == PaidCampaign.owner_id,
                Post.deleted_at.is_(None),
                Post.status == ContentStatus.PUBLISHED,
                Post.moderation_status == "approved",
                Post.visibility == "public",
                User.deleted_at.is_(None),
                User.status == AccountStatus.ACTIVE,
                or_(
                    Profile.id.is_(None),
                    (Profile.deleted_at.is_(None) & Profile.private_account.is_(False)),
                ),
            )
            .order_by(PaidCampaign.created_at, PaidCampaign.id)
            .limit(limit)
        )
        if campaign_id is not None:
            stmt = stmt.where(PaidCampaign.id == campaign_id)
        if campaign_ids is not None:
            stmt = stmt.where(PaidCampaign.id.in_(campaign_ids))
        return list((await self.db.execute(stmt)).scalars().all())
