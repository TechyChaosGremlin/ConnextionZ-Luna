"""Canonical onboarding categories and profile selections."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import Base


profile_categories = Table(
    "profile_categories",
    Base.metadata,
    Column(
        "profile_id",
        PG_UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("ix_profile_categories_category_id", "category_id"),
)


class Category(Base):
    """A canonical category selectable during onboarding."""

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_categories_name_nonempty"),
        CheckConstraint("length(slug) > 0", name="ck_categories_slug_nonempty"),
    )

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    profiles: Mapped[list["Profile"]] = relationship(
        "Profile", secondary=profile_categories, back_populates="categories"
    )
