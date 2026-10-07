"""Internal server selections and their authoritative Paid impressions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PaidDelivery(Base, TimestampMixin):
    """A server selection; only rows with impressed_at count as impressions."""

    __tablename__ = "paid_deliveries"
    __table_args__ = (
        CheckConstraint(
            "impressed_at IS NULL OR impressed_at >= selected_at",
            name="ck_paid_deliveries_time",
        ),
        Index(
            "ix_paid_deliveries_campaign_viewer_time", "campaign_id", "viewer_id", "impressed_at"
        ),
        Index("ix_paid_deliveries_post", "post_id"),
        Index("ix_paid_deliveries_viewer", "viewer_id"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("paid_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    # Retain the accounting row when a viewer is deleted. Their random UUID
    # remains the campaign-local reach/dedup identity; no profile data is stored.
    viewer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    impressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaidInteractionContext(Base):
    """One-use, short-lived capability for an interaction initiated from Paid Discovery."""

    __tablename__ = "paid_interaction_contexts"
    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_paid_interaction_context_delivery"),
        UniqueConstraint("token_hash", name="uq_paid_interaction_context_token_hash"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at <= expires_at",
            name="ck_paid_interaction_context_consumption_time",
        ),
        Index("ix_paid_interaction_context_expiry", "expires_at"),
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("paid_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    viewer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
