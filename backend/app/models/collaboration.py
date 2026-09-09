"""
Collaboration models — proposals, agreements, and milestones.

Covers the Collaboration Button and Collaboration Marketplace features.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


# ── Enums ────────────────────────────────────────────────────────


class CollaborationStatus(str, enum.Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MilestoneStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISPUTED = "disputed"


# ── Collaboration ────────────────────────────────────────────────

class Collaboration(Base, TimestampMixin, SoftDeleteMixin):
    """A collaboration between two or more creators."""

    __tablename__ = "collaborations"
    status_choices = tuple(status.value for status in CollaborationStatus)

    # Initiator
    initiator_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Details
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CollaborationStatus] = mapped_column(
        Enum(CollaborationStatus, name="collaboration_status"),
        nullable=False,
        default=CollaborationStatus.PROPOSED,
    )

    # Scope
    content_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # "video", "podcast", "livestream", etc.
    platform: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # "youtube", "tiktok", etc.
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Timeline
    proposed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Compensation
    budget_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_currency: Mapped[str] = mapped_column(
        String(3), default="USD", nullable=False
    )

    # Relationships
    participants: Mapped[list["CollaborationParticipant"]] = relationship(
        "CollaborationParticipant",
        back_populates="collaboration",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    milestones: Mapped[list["Milestone"]] = relationship(
        "Milestone",
        back_populates="collaboration",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def _update_collaboration(self, new_status: CollaborationStatus | str) -> None:
        """Validate and apply a collaboration status transition."""
        try:
            next_status = CollaborationStatus(new_status)
        except ValueError as exc:
            raise ValueError(f"Invalid status: {new_status}") from exc

        if next_status == CollaborationStatus.IN_PROGRESS:
            if self.status != CollaborationStatus.ACCEPTED:
                raise ValueError(
                    "Cannot transition to IN_PROGRESS from non-ACCEPTED status"
                )

        self.status = next_status

    def __repr__(self) -> str:
        return f"<Collaboration id={self.id!r} title={self.title!r} status={self.status.value!r}>"


# ── CollaborationParticipant ─────────────────────────────────────


class CollaborationParticipant(Base, TimestampMixin):
    """Join table linking users to collaborations with a role."""

    __tablename__ = "collaboration_participants"
    __table_args__ = (
        UniqueConstraint(
            "collaboration_id", "user_id", name="uq_collaboration_participant"
        ),
    )

    collaboration_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("collaborations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(64), default="participant", nullable=False
    )  # "initiator", "participant", "sponsor"
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accepted_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    collaboration: Mapped["Collaboration"] = relationship(
        "Collaboration", back_populates="participants"
    )

    def __repr__(self) -> str:
        return f"<CollaborationParticipant collab={self.collaboration_id!r} user={self.user_id!r}>"


# ── Milestone ────────────────────────────────────────────────────


class Milestone(Base, TimestampMixin):
    """A deliverable milestone within a collaboration."""

    __tablename__ = "milestones"

    collaboration_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("collaborations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, name="milestone_status"),
        nullable=False,
        default=MilestoneStatus.PENDING,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    due_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    collaboration: Mapped["Collaboration"] = relationship(
        "Collaboration", back_populates="milestones"
    )

    def __repr__(self) -> str:
        return f"<Milestone id={self.id!r} title={self.title!r} status={self.status.value!r}>"