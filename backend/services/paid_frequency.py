"""Read-only frequency eligibility; unavailable attribution fails closed."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

import structlog

from app.models.paid_campaign import PaidCampaign
from repositories.paid_impression_history_repository import (
    PaidImpressionHistoryReader,
    PaidImpressionHistoryUnavailable,
)

logger = structlog.get_logger()


class FrequencyStatus(str, Enum):
    ALLOWED = "allowed"
    CAPPED = "capped"
    ATTRIBUTION_UNAVAILABLE = "attribution_unavailable"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class FrequencyEligibility:
    status: FrequencyStatus
    impressions: int | None = None
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status == FrequencyStatus.ALLOWED


class PaidFrequencyService:
    def __init__(self, history: PaidImpressionHistoryReader):
        self.history = history

    async def check(
        self, campaign: PaidCampaign, viewer_id: uuid.UUID, now: datetime
    ) -> FrequencyEligibility:
        cap, window = campaign.frequency_cap, campaign.frequency_window_seconds
        if cap is None and window is None:
            return FrequencyEligibility(FrequencyStatus.ALLOWED)
        if type(cap) is not int or type(window) is not int or cap <= 0 or window <= 0:
            raise ValueError(
                "Frequency cap and window must be positive integers specified together"
            )
        if now.utcoffset() is None:
            raise ValueError("Frequency evaluation time must include a timezone")
        moment = now.astimezone(timezone.utc)
        start_at = moment - timedelta(seconds=window)
        try:
            count = await self.history.count_paid_impressions(
                viewer_id=viewer_id, campaign_id=campaign.id, start_at=start_at, end_at=moment
            )
        except PaidImpressionHistoryUnavailable as exc:
            logger.warning(
                "paid_frequency.attribution_unavailable",
                campaign_id=str(campaign.id),
                reason=str(exc),
            )
            return FrequencyEligibility(FrequencyStatus.ATTRIBUTION_UNAVAILABLE, reason=str(exc))
        if type(count) is not int or count < 0:
            raise ValueError("Paid impression history must return a nonnegative integer count")
        return FrequencyEligibility(
            FrequencyStatus.ALLOWED if count < cap else FrequencyStatus.CAPPED,
            impressions=count,
        )
