"""Provider-neutral payment state for collaboration marketplace agreements."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.collaboration import Collaboration


class CollaborationPaymentState(str, enum.Enum):
    PENDING = "PENDING"
    AUTHORIZED_HELD = "AUTHORIZED_HELD"
    COLLABORATION_ACTIVE = "COLLABORATION_ACTIVE"
    COMPLETED = "COMPLETED"
    RELEASE_PENDING = "RELEASE_PENDING"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"


class CollaborationPayment(Base, TimestampMixin):
    """One internal payment record for a collaboration; no money is processed."""

    __tablename__ = "collaboration_payments"
    __table_args__ = (
        UniqueConstraint("collaboration_id", name="uq_collaboration_payments_collaboration"),
        UniqueConstraint(
            "payment_provider",
            "provider_reference",
            name="uq_collaboration_payments_provider_reference",
        ),
        UniqueConstraint(
            "hold_idempotency_key",
            name="uq_collaboration_payments_hold_idempotency_key",
        ),
        CheckConstraint(
            "amount_minor_units > 0",
            name="ck_collaboration_payments_positive_amount",
        ),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_collaboration_payments_currency",
        ),
        CheckConstraint(
            "payer_user_id <> recipient_user_id",
            name="ck_collaboration_payments_distinct_parties",
        ),
        CheckConstraint(
            "(payment_provider IS NULL AND provider_reference IS NULL) OR "
            "(payment_provider IS NOT NULL AND provider_reference IS NOT NULL)",
            name="ck_collaboration_payments_provider_reference_pair",
        ),
        CheckConstraint(
            "(hold_idempotency_key IS NULL AND authorized_at IS NULL "
            "AND authorized_by_operation IS NULL) OR "
            "(hold_idempotency_key IS NOT NULL AND authorized_at IS NOT NULL "
            "AND authorized_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_hold_authorization_audit",
        ),
        CheckConstraint(
            "state NOT IN ('AUTHORIZED_HELD', 'COLLABORATION_ACTIVE', 'COMPLETED', "
            "'RELEASE_PENDING', 'RELEASED', 'REFUNDED', 'DISPUTED') OR "
            "(hold_idempotency_key IS NOT NULL AND authorized_at IS NOT NULL "
            "AND authorized_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_held_states_authorized",
        ),
        CheckConstraint(
            "(hold_reversed_at IS NULL AND hold_reversed_by_operation IS NULL) OR "
            "(hold_reversed_at IS NOT NULL AND hold_reversed_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_hold_reversal_audit",
        ),
        CheckConstraint(
            "completion_confirmed_by_operation IS NULL OR completed_at IS NOT NULL",
            name="ck_collaboration_payments_completion_audit",
        ),
        CheckConstraint(
            "release_authorized_by_operation IS NULL OR release_pending_at IS NOT NULL",
            name="ck_collaboration_payments_release_authorization_audit",
        ),
        CheckConstraint(
            "(refund_authorized_at IS NULL AND "
            "refund_authorized_by_operation IS NULL) OR "
            "(refund_authorized_at IS NOT NULL AND "
            "refund_authorized_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_refund_authorization_audit",
        ),
        CheckConstraint(
            "state <> 'REFUNDED' OR "
            "(refund_authorized_at IS NOT NULL AND "
            "refund_authorized_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_refunded_has_authorization",
        ),
        CheckConstraint(
            "state <> 'DISPUTED' OR "
            "(disputed_at IS NOT NULL AND disputed_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_dispute_initiation_audit",
        ),
        CheckConstraint(
            "(dispute_resolved_at IS NULL AND "
            "dispute_resolved_by_operation IS NULL) OR "
            "(dispute_resolved_at IS NOT NULL AND "
            "dispute_resolved_by_operation IS NOT NULL)",
            name="ck_collaboration_payments_dispute_resolution_audit",
        ),
        CheckConstraint(
            "cancelled_by_operation IS NULL OR cancelled_at IS NOT NULL",
            name="ck_collaboration_payments_cancellation_audit",
        ),
    )

    collaboration_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("collaborations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payer_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[CollaborationPaymentState] = mapped_column(
        Enum(
            CollaborationPaymentState,
            name="collaboration_payment_state",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=CollaborationPaymentState.PENDING,
        server_default=CollaborationPaymentState.PENDING.value,
    )

    payment_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    hold_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authorization_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authorized_by_operation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_reversed_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    collaboration_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_confirmed_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    release_pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_authorized_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disputed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    refund_authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    refund_authorized_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    disputed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    disputed_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    dispute_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    dispute_resolved_by_operation: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    collaboration: Mapped["Collaboration"] = relationship(
        "Collaboration",
        back_populates="payment",
    )
