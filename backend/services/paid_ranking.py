"""Read-only soft pacing for campaigns that have passed every Paid eligibility gate."""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction

from app.models.paid_campaign import PaidCampaign
from services.paid_campaign_service import utc_time


class PaidRankingService:
    @staticmethod
    def rank_eligible(campaigns: list[PaidCampaign], *, now: datetime) -> list[PaidCampaign]:
        """Order eligible campaigns; this is neither eligibility nor delivery authorization."""
        if now.utcoffset() is None:
            raise ValueError("Paid ranking time must include a timezone")
        moment = utc_time(now)

        def key(campaign: PaidCampaign) -> tuple[Fraction, Fraction, int, int]:
            duration = utc_time(campaign.end_at) - utc_time(campaign.start_at)
            if duration <= timedelta(0):
                raise ValueError("Campaign end must be after start")
            elapsed = moment - utc_time(campaign.start_at)
            elapsed_fraction = min(
                Fraction(1),
                max(
                    Fraction(0),
                    Fraction(
                        elapsed // timedelta(microseconds=1),
                        duration // timedelta(microseconds=1),
                    ),
                ),
            )
            progress = [
                Fraction(delivered, cap)
                for delivered, cap in (
                    (campaign.impressions_delivered, campaign.max_impressions),
                    (campaign.reach_delivered, campaign.max_reach),
                )
                if cap is not None
            ]
            # The closest delivery cap governs pacing; spend is only an eligibility gate.
            consumed = max(progress, default=Fraction(0))
            deficit = elapsed_fraction - consumed if progress else Fraction(0)
            return (
                -deficit,
                -(1 - consumed),
                campaign.impressions_delivered,
                campaign.id.int,
            )

        return sorted(campaigns, key=key)
