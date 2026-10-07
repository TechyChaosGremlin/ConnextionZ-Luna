"""Read boundary for reliable server-attributed Paid campaign impressions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy import case, distinct, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paid_campaign import PaidCampaign
from app.models.paid_delivery import PaidDelivery


class PaidImpressionHistoryUnavailable(RuntimeError):
    """Paid attribution/complete impression history has not been established."""


class PaidImpressionHistoryReader(Protocol):
    async def count_paid_impressions(
        self,
        *,
        viewer_id: uuid.UUID,
        campaign_id: uuid.UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> int:
        """Count distinct validated deliveries in [start_at, end_at], or raise unavailable."""
        ...


class PaidImpressionHistoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def count_paid_impressions(
        self,
        *,
        viewer_id: uuid.UUID,
        campaign_id: uuid.UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> int:
        if start_at.utcoffset() is None or end_at.utcoffset() is None or end_at < start_at:
            raise ValueError("Paid history requires an ordered timezone-aware window")
        statement = (
            select(
                PaidCampaign.impressions_delivered,
                PaidCampaign.reach_delivered,
                func.count(PaidDelivery.id).label("total"),
                func.count(distinct(PaidDelivery.viewer_id)).label("reach"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (PaidDelivery.viewer_id == viewer_id)
                                & (PaidDelivery.impressed_at >= start_at)
                                & (PaidDelivery.impressed_at <= end_at),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("viewer_count"),
            )
            .outerjoin(
                PaidDelivery,
                (PaidDelivery.campaign_id == PaidCampaign.id)
                & PaidDelivery.impressed_at.is_not(None),
            )
            .where(PaidCampaign.id == campaign_id)
            .group_by(PaidCampaign.id)
        )
        try:
            # A missing migration must not poison the caller's transaction.
            async with self.db.begin_nested():
                row = (await self.db.execute(statement)).one_or_none()
        except DBAPIError as exc:
            code = getattr(exc.orig, "sqlstate", None)
            if code is None:
                code = getattr(getattr(exc.orig, "__cause__", None), "sqlstate", None)
            if code == "42P01":
                raise PaidImpressionHistoryUnavailable(
                    "Paid delivery schema is unavailable"
                ) from exc
            raise
        if (
            row is None
            or row.total != row.impressions_delivered
            or row.reach != row.reach_delivered
        ):
            raise PaidImpressionHistoryUnavailable(
                "Paid delivery history is incomplete or inconsistent"
            )
        return int(row.viewer_count)
