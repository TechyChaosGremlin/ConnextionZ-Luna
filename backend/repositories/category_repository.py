"""Read canonical onboarding categories."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category, profile_categories
from app.models.user import Profile, User


class CategoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_slugs(self, slugs: list[str]) -> list[Category]:
        result = await self.db.execute(select(Category).where(Category.slug.in_(slugs)))
        return list(result.scalars().all())

    async def get_slugs_by_user_id(self, user_id: uuid.UUID) -> list[str]:
        """Return canonical category slugs selected by one user."""
        stmt = (
            select(Category.slug)
            .join(
                profile_categories,
                profile_categories.c.category_id == Category.id,
            )
            .join(Profile, Profile.id == profile_categories.c.profile_id)
            .join(User, User.id == Profile.user_id)
            .where(
                Profile.user_id == user_id,
                Profile.deleted_at.is_(None),
                User.deleted_at.is_(None),
            )
            .order_by(Category.slug)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
