"""Owner-only campaign management and read-only Paid candidate eligibility."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentStatus
from app.models.paid_campaign import PaidCampaign, PaidCampaignStatus
from app.models.user import AccountStatus, User
from repositories.content_repository import PostRepository
from repositories.paid_campaign_repository import PaidCampaignRepository
from repositories.paid_impression_history_repository import (
    PaidImpressionHistoryReader,
    PaidImpressionHistoryRepository,
)
from repositories.profile_repository import ProfileRepository
from repositories.social_repository import FeedSafetyRepository
from services.paid_frequency import FrequencyEligibility, FrequencyStatus, PaidFrequencyService
from services.paid_pagination import MAX_PAID_CANDIDATES, PaidCursor
from services.paid_targeting import targeting_matches, validate_targeting

MAX_CAMPAIGN_INTEGER = 2_147_483_647


def utc_time(value: datetime) -> datetime:
    # SQLite test round-trips lose the offset on DateTime(timezone=True).
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


@dataclass(frozen=True)
class CampaignConfiguration:
    start_at: datetime
    end_at: datetime
    budget_minor_units: int
    currency: str
    max_impressions: int | None = None
    max_reach: int | None = None
    frequency_cap: int | None = None
    frequency_window_seconds: int | None = None
    targeting: dict | None = None

    def validated(self) -> CampaignConfiguration:
        for name in ("start_at", "end_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone")
        if self.end_at <= self.start_at:
            raise ValueError("Campaign end must be after start")
        for name in (
            "budget_minor_units",
            "max_impressions",
            "max_reach",
            "frequency_cap",
            "frequency_window_seconds",
        ):
            value = getattr(self, name)
            if value is None and name != "budget_minor_units":
                continue
            if type(value) is not int or not 1 <= value <= MAX_CAMPAIGN_INTEGER:
                raise ValueError(f"{name} must be a positive 32-bit integer")
        if not isinstance(self.currency, str) or not re.fullmatch("[A-Z]{3}", self.currency):
            raise ValueError("Currency must be a three-letter uppercase code")
        if (self.frequency_cap is None) != (self.frequency_window_seconds is None):
            raise ValueError("Frequency cap and window must be specified together")
        targeting = validate_targeting(self.targeting)
        return replace(
            self,
            start_at=utc_time(self.start_at),
            end_at=utc_time(self.end_at),
            targeting=targeting,
        )


def campaign_is_expired(campaign: PaidCampaign, now: datetime) -> bool:
    return utc_time(now) >= utc_time(campaign.end_at)


def campaign_is_exhausted(campaign: PaidCampaign) -> bool:
    return (
        campaign.spent_minor_units >= campaign.budget_minor_units
        or (
            campaign.max_impressions is not None
            and campaign.impressions_delivered >= campaign.max_impressions
        )
        or (campaign.max_reach is not None and campaign.reach_delivered >= campaign.max_reach)
    )


def campaign_is_active(campaign: PaidCampaign, now: datetime) -> bool:
    """Campaign-level eligibility only, not permission to deliver an impression."""
    return (
        campaign.status == PaidCampaignStatus.ACTIVE
        and utc_time(campaign.start_at) <= utc_time(now)
        and not campaign_is_expired(campaign, now)
        and not campaign_is_exhausted(campaign)
    )


@dataclass(frozen=True)
class PaidCandidate:
    """Public-safe campaign-derived identity; not delivery authorization or accounting."""

    post_id: uuid.UUID
    paid_campaign_id: uuid.UUID

    @property
    def is_sponsored(self) -> bool:
        return True

    @property
    def sponsored_label(self) -> str:
        return "Sponsored"


@dataclass(frozen=True)
class PaidCandidatePage:
    items: list[PaidCandidate]
    next_cursor: str | None = None


@dataclass(frozen=True)
class PaidEligibility:
    campaign_eligible: bool
    targeting_matches: bool
    frequency: FrequencyEligibility

    @property
    def eligible(self) -> bool:
        return self.campaign_eligible and self.targeting_matches and self.frequency.allowed


TRANSITIONS = {
    PaidCampaignStatus.DRAFT: {
        PaidCampaignStatus.SCHEDULED,
        PaidCampaignStatus.ACTIVE,
        PaidCampaignStatus.CANCELLED,
    },
    PaidCampaignStatus.SCHEDULED: {
        PaidCampaignStatus.ACTIVE,
        PaidCampaignStatus.PAUSED,
        PaidCampaignStatus.COMPLETED,
        PaidCampaignStatus.CANCELLED,
    },
    PaidCampaignStatus.ACTIVE: {
        PaidCampaignStatus.PAUSED,
        PaidCampaignStatus.EXHAUSTED,
        PaidCampaignStatus.COMPLETED,
        PaidCampaignStatus.CANCELLED,
    },
    PaidCampaignStatus.PAUSED: {
        PaidCampaignStatus.SCHEDULED,
        PaidCampaignStatus.ACTIVE,
        PaidCampaignStatus.EXHAUSTED,
        PaidCampaignStatus.COMPLETED,
        PaidCampaignStatus.CANCELLED,
    },
    PaidCampaignStatus.EXHAUSTED: set(),
    PaidCampaignStatus.COMPLETED: set(),
    PaidCampaignStatus.CANCELLED: set(),
}


class PaidCampaignService:
    def __init__(
        self, db: AsyncSession, *, impression_history: PaidImpressionHistoryReader | None = None
    ):
        self.db = db
        self.repository = PaidCampaignRepository(db)
        self.frequency = PaidFrequencyService(
            PaidImpressionHistoryRepository(db)
            if impression_history is None
            else impression_history
        )

    @staticmethod
    def require_active_user(user: User) -> None:
        if user.status != AccountStatus.ACTIVE or user.deleted_at is not None:
            raise PermissionError("Account is not active")

    async def _validate_post(self, owner: User, post_id: uuid.UUID) -> None:
        post = await PostRepository(self.db).get_by_id(post_id)
        if post is None:
            raise ValueError("Post not found")
        if post.user_id != owner.id:
            raise PermissionError("Only the post author can promote this post")
        if (
            post.status != ContentStatus.PUBLISHED
            or post.moderation_status != "approved"
            or post.visibility != "public"
        ):
            raise ValueError("Only approved, published, public posts can be promoted")
        profile = await ProfileRepository(self.db).get_by_user_id(owner.id)
        if profile is not None and (profile.private_account or profile.deleted_at is not None):
            raise ValueError("Private or deleted profiles cannot promote posts")

    async def create_campaign(
        self, owner: User, post_id: uuid.UUID, configuration: CampaignConfiguration
    ) -> PaidCampaign:
        self.require_active_user(owner)
        config = configuration.validated()
        await self._validate_post(owner, post_id)
        return await self.repository.create(
            PaidCampaign(
                owner_id=owner.id,
                post_id=post_id,
                status=PaidCampaignStatus.DRAFT,
                spent_minor_units=0,
                impressions_delivered=0,
                reach_delivered=0,
                **asdict(config),
            )
        )

    async def get_campaign(self, owner: User, campaign_id: uuid.UUID) -> PaidCampaign:
        self.require_active_user(owner)
        campaign = await self.repository.get_owned(campaign_id, owner.id)
        if campaign is None:
            raise ValueError("Campaign not found")
        return campaign

    async def update_campaign(
        self,
        owner: User,
        campaign_id: uuid.UUID,
        *,
        configuration: CampaignConfiguration | None = None,
        status: PaidCampaignStatus | None = None,
        now: datetime | None = None,
    ) -> PaidCampaign:
        self.require_active_user(owner)
        campaign = await self.repository.get_owned(campaign_id, owner.id, for_update=True)
        if campaign is None:
            raise ValueError("Campaign not found")
        if configuration is None and status is None:
            raise ValueError("No campaign update specified")
        moment = utc_time(now or datetime.now(timezone.utc))
        config = configuration.validated() if configuration is not None else None
        if config is not None:
            if campaign.status not in (PaidCampaignStatus.DRAFT, PaidCampaignStatus.PAUSED):
                raise ValueError("Only draft or paused campaign configuration can be edited")
            if config.currency != campaign.currency and (
                campaign.spent_minor_units or campaign.impressions_delivered
            ):
                raise ValueError("Currency cannot change after spending or delivery")
            if (
                config.budget_minor_units < campaign.spent_minor_units
                or (
                    config.max_impressions is not None
                    and config.max_impressions < campaign.impressions_delivered
                )
                or (config.max_reach is not None and config.max_reach < campaign.reach_delivered)
            ):
                raise ValueError("Campaign limits cannot be below consumed capacity")
        next_status = campaign.status if status is None else status
        if not isinstance(next_status, PaidCampaignStatus):
            raise ValueError("Invalid campaign status")
        if next_status != campaign.status and next_status not in TRANSITIONS[campaign.status]:
            raise ValueError("Invalid campaign status transition")
        proposed = PaidCampaign(
            status=next_status,
            start_at=campaign.start_at if config is None else config.start_at,
            end_at=campaign.end_at if config is None else config.end_at,
            budget_minor_units=(
                campaign.budget_minor_units if config is None else config.budget_minor_units
            ),
            spent_minor_units=campaign.spent_minor_units,
            impressions_delivered=campaign.impressions_delivered,
            reach_delivered=campaign.reach_delivered,
            max_impressions=campaign.max_impressions if config is None else config.max_impressions,
            max_reach=campaign.max_reach if config is None else config.max_reach,
        )
        if next_status == PaidCampaignStatus.ACTIVE:
            validate_targeting(campaign.targeting if config is None else config.targeting)
            if not campaign_is_active(proposed, moment):
                raise ValueError(
                    "Campaign cannot activate outside its window or with exhausted capacity"
                )
            await self._validate_post(owner, campaign.post_id)
        if next_status == PaidCampaignStatus.SCHEDULED and utc_time(proposed.start_at) <= moment:
            raise ValueError("Scheduled campaign must start in the future")
        if next_status == PaidCampaignStatus.EXHAUSTED and not campaign_is_exhausted(proposed):
            raise ValueError("Campaign capacity is not exhausted")
        if next_status == PaidCampaignStatus.COMPLETED and not campaign_is_expired(
            proposed, moment
        ):
            raise ValueError("Campaign has not ended")
        if config is not None:
            for name, value in asdict(config).items():
                setattr(campaign, name, value)
        campaign.status = next_status
        return await self.repository.update(campaign)

    async def get_eligible_candidates(
        self, viewer: User, *, now: datetime | None = None, limit: int = 100
    ) -> list[PaidCandidate]:
        """Apply targeting/frequency after campaign and safety gates, without delivery."""
        campaigns = await self._get_eligible_campaigns(
            viewer, now=utc_time(now or datetime.now(timezone.utc)), limit=limit
        )
        return [
            PaidCandidate(post_id=campaign.post_id, paid_campaign_id=campaign.id)
            for campaign in campaigns
        ]

    async def get_ranked_candidates(
        self, viewer: User, *, now: datetime | None = None, limit: int = 100
    ) -> list[PaidCandidate]:
        """Rank only the bounded, fully eligible Paid pool, without recording delivery."""
        from services.paid_ranking import PaidRankingService

        moment = utc_time(now or datetime.now(timezone.utc))
        campaigns = await self._get_eligible_campaigns(viewer, now=moment, limit=limit)
        return [
            PaidCandidate(post_id=campaign.post_id, paid_campaign_id=campaign.id)
            for campaign in PaidRankingService.rank_eligible(campaigns, now=moment)
        ]

    async def get_candidate_page(
        self,
        viewer: User,
        *,
        cursor: str | None = None,
        limit: int = 10,
        now: datetime | None = None,
    ) -> PaidCandidatePage:
        """Page one ranked snapshot; revalidate every continuation without delivery."""
        self.require_active_user(viewer)
        if not 1 <= limit <= MAX_PAID_CANDIDATES:
            raise ValueError("Paid page limit must be between 1 and 100")
        moment = utc_time(now or datetime.now(timezone.utc))
        if cursor is None:
            ranked = await self.get_ranked_candidates(viewer, now=moment, limit=MAX_PAID_CANDIDATES)
            seen_posts: set[uuid.UUID] = set()
            unique = []
            for candidate in ranked:
                if candidate.post_id not in seen_posts:
                    unique.append(candidate)
                    seen_posts.add(candidate.post_id)
            identities = tuple(
                (candidate.paid_campaign_id, candidate.post_id) for candidate in unique
            )
            remaining = unique
        else:
            snapshot = PaidCursor.decode(cursor, viewer.id)
            identities = snapshot.identities
            campaigns = await self._get_eligible_campaigns(
                viewer,
                now=moment,
                limit=MAX_PAID_CANDIDATES,
                campaign_ids=tuple(campaign_id for campaign_id, _ in identities),
            )
            eligible = {campaign.id: campaign for campaign in campaigns}
            anchor = next(
                index
                for index, (campaign_id, _) in enumerate(identities)
                if campaign_id == snapshot.last_campaign_id
            )
            remaining = [
                PaidCandidate(post_id=post_id, paid_campaign_id=campaign_id)
                for campaign_id, post_id in identities[anchor + 1 :]
                if campaign_id in eligible and eligible[campaign_id].post_id == post_id
            ]
        page = remaining[:limit]
        next_cursor = (
            PaidCursor(identities, page[-1].paid_campaign_id).encode(viewer.id)
            if len(remaining) > limit
            else None
        )
        return PaidCandidatePage(items=page, next_cursor=next_cursor)

    async def _get_eligible_campaigns(
        self,
        viewer: User,
        *,
        now: datetime,
        limit: int,
        campaign_ids: tuple[uuid.UUID, ...] | None = None,
    ) -> list[PaidCampaign]:
        self.require_active_user(viewer)
        campaigns = await self.repository.get_eligible(now, limit=limit, campaign_ids=campaign_ids)
        hidden_ids = await FeedSafetyRepository(self.db).get_hidden_creator_ids(
            viewer.id, list({campaign.owner_id for campaign in campaigns})
        )
        visible = [campaign for campaign in campaigns if campaign.owner_id not in hidden_ids]
        viewer_tags = await self._viewer_tags(viewer, visible)
        eligible_campaigns = []
        for campaign in visible:
            eligibility = await self._evaluate_eligibility(campaign, viewer, viewer_tags, now)
            if eligibility.eligible:
                eligible_campaigns.append(campaign)
        return eligible_campaigns

    async def _viewer_tags(self, viewer: User, campaigns: list[PaidCampaign]) -> object:
        if not any(campaign.targeting for campaign in campaigns):
            return None
        profile = await ProfileRepository(self.db).get_by_user_id(viewer.id)
        if profile is None or profile.deleted_at is not None:
            return None
        return profile.tags

    async def evaluate_eligibility(
        self, campaign: PaidCampaign, viewer: User, *, now: datetime | None = None
    ) -> PaidEligibility:
        """Evaluate campaign/targeting/frequency gates, not post safety or delivery."""
        self.require_active_user(viewer)
        viewer_tags = await self._viewer_tags(viewer, [campaign])
        return await self._evaluate_eligibility(
            campaign, viewer, viewer_tags, utc_time(now or datetime.now(timezone.utc))
        )

    async def _evaluate_eligibility(
        self, campaign: PaidCampaign, viewer: User, viewer_tags: object, now: datetime
    ) -> PaidEligibility:
        active = campaign_is_active(campaign, now)
        matches = targeting_matches(campaign.targeting, viewer_tags)
        if not active or not matches:
            return PaidEligibility(
                active, matches, FrequencyEligibility(FrequencyStatus.NOT_CHECKED)
            )
        return PaidEligibility(
            active, matches, await self.frequency.check(campaign, viewer.id, now)
        )
