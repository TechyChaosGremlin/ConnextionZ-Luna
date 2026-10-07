"""Owner-only GraphQL campaign contracts; never ordinary feed campaign details."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

import strawberry
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paid_campaign import PaidCampaign, PaidCampaignStatus
from app.models.user import User
from services.paid_campaign_service import CampaignConfiguration, PaidCampaignService, utc_time

strawberry.enum(PaidCampaignStatus)


class CampaignContext(Protocol):
    db: AsyncSession

    def require_auth(self) -> User: ...


@strawberry.input
class PaidCampaignConfigurationInput:
    start_at: datetime
    end_at: datetime
    budget_minor_units: int
    currency: str
    max_impressions: int | None = None
    max_reach: int | None = None
    frequency_cap: int | None = None
    frequency_window_seconds: int | None = None
    targeting: object | None = None

    def to_domain(self) -> CampaignConfiguration:
        if self.targeting is not None and not isinstance(self.targeting, dict):
            raise ValueError("Targeting must be a JSON object")
        return CampaignConfiguration(
            start_at=self.start_at,
            end_at=self.end_at,
            budget_minor_units=self.budget_minor_units,
            currency=self.currency,
            max_impressions=self.max_impressions,
            max_reach=self.max_reach,
            frequency_cap=self.frequency_cap,
            frequency_window_seconds=self.frequency_window_seconds,
            targeting=self.targeting,
        )


@strawberry.input
class CreatePaidCampaignInput:
    post_id: uuid.UUID
    configuration: PaidCampaignConfigurationInput


@strawberry.input
class UpdatePaidCampaignInput:
    configuration: PaidCampaignConfigurationInput | None = None
    status: PaidCampaignStatus | None = None


@strawberry.type
class PaidCampaignType:
    id: uuid.UUID
    owner_id: uuid.UUID
    post_id: uuid.UUID
    status: PaidCampaignStatus
    start_at: datetime
    end_at: datetime
    budget_minor_units: int
    currency: str
    spent_minor_units: int
    impressions_delivered: int
    reach_delivered: int
    max_impressions: int | None
    max_reach: int | None
    frequency_cap: int | None
    frequency_window_seconds: int | None
    targeting: object | None
    created_at: datetime
    updated_at: datetime


def campaign_to_type(campaign: PaidCampaign) -> PaidCampaignType:
    return PaidCampaignType(
        id=campaign.id,
        owner_id=campaign.owner_id,
        post_id=campaign.post_id,
        status=campaign.status,
        start_at=utc_time(campaign.start_at),
        end_at=utc_time(campaign.end_at),
        budget_minor_units=campaign.budget_minor_units,
        currency=campaign.currency,
        spent_minor_units=campaign.spent_minor_units,
        impressions_delivered=campaign.impressions_delivered,
        reach_delivered=campaign.reach_delivered,
        max_impressions=campaign.max_impressions,
        max_reach=campaign.max_reach,
        frequency_cap=campaign.frequency_cap,
        frequency_window_seconds=campaign.frequency_window_seconds,
        targeting=campaign.targeting,
        created_at=utc_time(campaign.created_at),
        updated_at=utc_time(campaign.updated_at),
    )


async def paid_campaign(ctx: CampaignContext, campaign_id: uuid.UUID) -> PaidCampaignType:
    campaign = await PaidCampaignService(ctx.db).get_campaign(ctx.require_auth(), campaign_id)
    return campaign_to_type(campaign)


async def create_paid_campaign(
    ctx: CampaignContext, input: CreatePaidCampaignInput
) -> PaidCampaignType:
    owner = ctx.require_auth()
    campaign = await PaidCampaignService(ctx.db).create_campaign(
        owner, input.post_id, input.configuration.to_domain()
    )
    await ctx.db.commit()
    return campaign_to_type(campaign)


async def update_paid_campaign(
    ctx: CampaignContext, campaign_id: uuid.UUID, input: UpdatePaidCampaignInput
) -> PaidCampaignType:
    owner = ctx.require_auth()
    campaign = await PaidCampaignService(ctx.db).update_campaign(
        owner,
        campaign_id,
        configuration=input.configuration.to_domain() if input.configuration is not None else None,
        status=input.status,
    )
    await ctx.db.commit()
    return campaign_to_type(campaign)
