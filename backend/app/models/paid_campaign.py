"""Campaign relationships for the separate Paid discovery algorithm."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PaidCampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    EXHAUSTED = "exhausted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaidCampaign(Base, TimestampMixin):
    __tablename__ = "paid_campaigns"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_paid_campaigns_window"),
        CheckConstraint("budget_minor_units > 0", name="ck_paid_campaigns_budget"),
        CheckConstraint(
            "spent_minor_units >= 0 AND spent_minor_units <= budget_minor_units",
            name="ck_paid_campaigns_spend",
        ),
        CheckConstraint(
            "impressions_delivered >= 0 AND reach_delivered >= 0 "
            "AND reach_delivered <= impressions_delivered",
            name="ck_paid_campaigns_delivery",
        ),
        CheckConstraint(
            "max_impressions IS NULL OR "
            "(max_impressions > 0 AND impressions_delivered <= max_impressions)",
            name="ck_paid_campaigns_impressions",
        ),
        CheckConstraint(
            "max_reach IS NULL OR (max_reach > 0 AND reach_delivered <= max_reach)",
            name="ck_paid_campaigns_reach",
        ),
        CheckConstraint(
            "(frequency_cap IS NULL AND frequency_window_seconds IS NULL) OR "
            "(frequency_cap IS NOT NULL AND frequency_window_seconds IS NOT NULL "
            "AND frequency_cap > 0 AND frequency_window_seconds > 0)",
            name="ck_paid_campaigns_frequency",
        ),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_paid_campaigns_currency",
        ),
        Index("ix_paid_campaigns_owner_created", "owner_id", "created_at"),
        Index("ix_paid_campaigns_post", "post_id"),
        Index("ix_paid_campaigns_status_window", "status", "start_at", "end_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PaidCampaignStatus] = mapped_column(
        Enum(
            PaidCampaignStatus,
            name="paid_campaign_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=PaidCampaignStatus.DRAFT,
        server_default="draft",
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    budget_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    spent_minor_units: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    impressions_delivered: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    reach_delivered: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    max_impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency_window_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    targeting: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
