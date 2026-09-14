"""
Base repository class for generic CRUD operations.

Provides a generic base class for repository implementations with
common CRUD operations using SQLAlchemy async sessions.
"""

from __future__ import annotations

from typing import Generic, TypeVar, Type, Optional
from uuid import UUID

from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.base import Base

# Generic type for the model
T = TypeVar('T', bound=Base)


class BaseRepository(Generic[T]):
    """
    Base repository class with generic CRUD operations.

    Type Parameters:
        T: The SQLAlchemy model type (must extend Base)

    Attributes:
        db: AsyncSession for database operations
        model_class: The model class this repository handles
    """

    def __init__(self, db: AsyncSession, model_class: Type[T]):
        """
        Initialize repository with database session and model class.

        Args:
            db: AsyncSession for database operations
            model_class: The SQLAlchemy model class
        """
        self.db = db
        self.model_class = model_class

    async def create(self, entity: T) -> T:
        """
        Create a new entity.

        Args:
            entity: Entity instance to create

        Returns:
            Created entity with updated fields (e.g., ID)
        """
        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def get_by_id(self, entity_id: UUID | str) -> Optional[T]:
        """
        Get entity by ID.

        Args:
            entity_id: Entity ID (UUID or string)

        Returns:
            Entity instance or None if not found
        """
        stmt = select(self.model_class).where(self.model_class.id == entity_id)
        stmt = self._apply_soft_delete_filter(stmt)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        order_by: Optional[str] = None,
        **filters
    ) -> list[T]:
        """
        Get all entities with pagination and filtering.

        Args:
            skip: Number of records to skip (offset)
            limit: Maximum number of records to return
            order_by: Column name to order by (prefix with '-' for DESC)
            **filters: Column name/value pairs for filtering

        Returns:
            List of entities
        """
        stmt = select(self.model_class)

        # Apply filters
        for column, value in filters.items():
            if hasattr(self.model_class, column):
                stmt = stmt.where(getattr(self.model_class, column) == value)

        # Apply ordering
        if order_by:
            if order_by.startswith('-'):
                col_name = order_by[1:]
                if hasattr(self.model_class, col_name):
                    stmt = stmt.order_by(getattr(self.model_class, col_name).desc())
            else:
                if hasattr(self.model_class, order_by):
                    stmt = stmt.order_by(getattr(self.model_class, order_by).asc())

        # Apply pagination
        stmt = stmt.offset(skip).limit(limit)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(self, **filters) -> int:
        """
        Count entities with optional filtering.

        Args:
            **filters: Column name/value pairs for filtering

        Returns:
            Count of matching entities
        """
        stmt = select(func.count()).select_from(self.model_class)

        # Apply filters
        for column, value in filters.items():
            if hasattr(self.model_class, column):
                stmt = stmt.where(getattr(self.model_class, column) == value)

        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def update(self, entity: T) -> T:
        """
        Update an existing entity.

        Args:
            entity: Entity instance with updated values

        Returns:
            Updated entity
        """
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def delete(self, entity: T) -> None:
        """
        Delete an entity (hard delete).

        Args:
            entity: Entity instance to delete
        """
        await self.db.delete(entity)
        await self.db.flush()

    async def delete_by_id(self, entity_id: UUID | str) -> bool:
        """
        Delete an entity by ID (hard delete).

        Args:
            entity_id: Entity ID

        Returns:
            True if entity was deleted, False if not found
        """
        entity = await self.get_by_id(entity_id)
        if entity:
            await self.delete(entity)
            return True
        return False

    async def exists(self, entity_id: UUID | str) -> bool:
        """
        Check if entity exists by ID.

        Args:
            entity_id: Entity ID

        Returns:
            True if entity exists, False otherwise
        """
        stmt = select(func.count()).select_from(self.model_class).where(self.model_class.id == entity_id)
        stmt = self._apply_soft_delete_filter(stmt)
        result = await self.db.execute(stmt)
        return result.scalar_one() > 0

    def _apply_soft_delete_filter(self, stmt: Select) -> Select:
        """
        Apply soft delete filter if model supports it.

        Args:
            stmt: SQLAlchemy statement

        Returns:
            Modified statement with soft delete filter
        """
        if hasattr(self.model_class, 'deleted_at'):
            stmt = stmt.where(self.model_class.deleted_at.is_(None))
        return stmt
