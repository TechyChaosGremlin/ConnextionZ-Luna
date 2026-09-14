"""
Content repository for database operations.

Provides CRUD operations for Post, Comment, and Media models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, case, desc, func, or_, select, String, cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Post, Comment, Media, ContentType, ContentStatus
from app.models.social import UserBlock, UserMute
from app.models.user import AccountStatus, Profile, User
from repositories.base import BaseRepository


class PostRepository(BaseRepository[Post]):
    """Repository for Post model database operations.

    Extends BaseRepository with common CRUD operations and adds
    post-specific query methods for feeds, pagination, and interactions.
    """

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        super().__init__(db, Post)

    async def get_by_id(self, post_id: uuid.UUID, include_deleted: bool = False) -> Optional[Post]:
        """
        Get post by ID.

        Args:
            post_id: Post ID (UUID)
            include_deleted: Whether to include soft-deleted posts

        Returns:
            Post object or None if not found
        """
        stmt = select(Post).where(Post.id == post_id)
        if not include_deleted:
            stmt = stmt.where(Post.deleted_at.is_(None))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(
        self,
        user_id: uuid.UUID,
        status: Optional[ContentStatus] = None,
        limit: int = 20,
        before_id: Optional[uuid.UUID] = None,
    ) -> list[Post]:
        """
        Get posts by user, ordered by created_at DESC.

        Args:
            user_id: User ID to filter by
            status: Optional content status filter
            limit: Maximum number of posts to return
            before_id: Cursor for pagination (UUIDv7)

        Returns:
            List of Post objects
        """
        stmt = select(Post).where(
            Post.user_id == user_id,
            Post.deleted_at.is_(None),
        )
        if status:
            stmt = stmt.where(Post.status == status)
        if before_id:
            # Cursor-based pagination using UUIDv7 time-sort
            stmt = stmt.where(Post.id < before_id)
        stmt = stmt.order_by(Post.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_feed(
        self,
        user_ids: list[uuid.UUID],
        content_types: Optional[list[ContentType]] = None,
        limit: int = 20,
        before_id: Optional[uuid.UUID] = None,
    ) -> list[Post]:
        """
        Get a feed of published posts from specified users.

        Args:
            user_ids: List of user IDs to include in feed
            content_types: Optional filter by content types
            limit: Maximum number of posts to return
            before_id: Cursor for pagination (UUIDv7)

        Returns:
            List of Post objects
        """
        stmt = select(Post).where(
            Post.user_id.in_(user_ids),
            Post.status == ContentStatus.PUBLISHED,
            Post.deleted_at.is_(None),
        )
        if content_types:
            stmt = stmt.where(Post.content_type.in_(content_types))
        if before_id:
            stmt = stmt.where(Post.id < before_id)
        stmt = stmt.order_by(Post.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_discovery_pool(
        self,
        exclude_user_ids: Optional[list[uuid.UUID]] = None,
        since: Optional[datetime] = None,
        limit: int = 150,
    ) -> list[Post]:
        """Recent posts for "For You" candidate generation beyond the
        viewer's own follow graph (cold-start + reach beyond follows).

        Only content-status/soft-delete filtering happens here; visibility,
        moderation, and block/mute safety filtering happen downstream via
        the same checks used for the rest of the feed.
        """
        stmt = select(Post).where(
            Post.status == ContentStatus.PUBLISHED,
            Post.deleted_at.is_(None),
        )
        if exclude_user_ids:
            stmt = stmt.where(~Post.user_id.in_(exclude_user_ids))
        if since is not None:
            stmt = stmt.where(Post.created_at >= since)
        stmt = stmt.order_by(Post.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_interest_pool(
        self,
        interest_tags: list[str],
        exclude_user_ids: Optional[list[uuid.UUID]] = None,
        since: Optional[datetime] = None,
        limit: int = 60,
    ) -> list[Post]:
        """Posts matching the viewer's demonstrated interest tags ("For You").

        Supplements the follow/affinity pools with content the viewer has shown
        interest in (by topic) but may not follow. Only content-status/
        soft-delete filtering + tag overlap happens here; visibility,
        moderation, and block/mute safety filtering happen downstream via the
        same checks used for the rest of the feed.

        ``Post.tags`` is a JSONB array of strings, so overlap uses Postgres'
        jsonb ``?|`` operator (true when any of the given strings exists as an
        array element) — semantically exact element matching, GIN-indexable.
        """
        if not interest_tags:
            return []
        stmt = select(Post).where(
            Post.status == ContentStatus.PUBLISHED,
            Post.deleted_at.is_(None),
            Post.tags.op("?|")(cast(interest_tags, ARRAY(String))),
        )
        if exclude_user_ids:
            stmt = stmt.where(~Post.user_id.in_(exclude_user_ids))
        if since is not None:
            stmt = stmt.where(Post.created_at >= since)
        stmt = stmt.order_by(Post.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, post_id: uuid.UUID) -> bool:
        """
        Soft-delete a post.

        Args:
            post_id: Post ID to soft delete

        Returns:
            True if post was found and soft-deleted, False otherwise
        """
        post = await self.get_by_id(post_id)
        if not post:
            return False
        post.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
        return True

    async def like_post(self, post_id: uuid.UUID) -> int:
        """
        Increment like count on a post.

        Args:
            post_id: Post ID to like

        Returns:
            New like count

        Raises:
            ValueError: If post not found
        """
        post = await self.get_by_id(post_id)
        if not post:
            raise ValueError("Post not found")
        post.like_count += 1
        await self.db.flush()
        return post.like_count

    async def get_total_count(self) -> int:
        """
        Get total count of non-deleted posts.

        Returns:
            Count of active posts
        """
        result = await self.db.execute(
            select(func.count()).select_from(Post).where(Post.deleted_at.is_(None))
        )
        return result.scalar_one()

    async def search_by_content(
        self,
        query: str,
        viewer_id: uuid.UUID | None = None,
        following_ids: list[uuid.UUID] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[tuple[Post, Profile, float]]:
        """
        Search posts by caption, hashtags, audio, or creator with relevance ranking.
        
        Ranking algorithm (via SQL case expression):
        - Caption prefix match: 30
        - Caption contains match: 15
        - Hashtags contains: 25
        - Audio contains: 10
        - Profile (username/display_name) contains: 20
        
        Results sorted by: rank_score DESC, view_count DESC, id DESC

        Args:
            query: Search query string
            viewer_id: Authenticated viewer, when available
            following_ids: Users followed by the viewer
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of (Post, Profile, rank_score) tuples sorted by relevance
        """
        terms = [term for term in query.strip().split() if term]
        if not terms:
            return []

        # Build patterns for matching
        contains_pattern = "%" + "%".join(terms) + "%"
        prefix_pattern = terms[0] + "%"
        following_ids = following_ids or []

        # Build rank score using SQL case expression
        rank_score = (
            case((Post.caption.ilike(prefix_pattern), 30), else_=0)
            + case((Post.caption.ilike(contains_pattern), 15), else_=0)
            + case((cast(Post.hashtags, String).ilike(contains_pattern), 25), else_=0)
            + case((Post.audio.ilike(contains_pattern), 10), else_=0)
            + case(
                (
                    or_(
                        User.username.ilike(contains_pattern),
                        Profile.display_name.ilike(contains_pattern),
                    ),
                    20,
                ),
                else_=0,
            )
        )

        # Build filters: at least one term must match in caption, hashtags, audio, or profile
        filters = []
        for term in terms:
            pattern = f"%{term}%"
            filters.append(
                or_(
                    Post.caption.ilike(pattern),
                    Post.audio.ilike(pattern),
                    cast(Post.hashtags, String).ilike(pattern),
                    User.username.ilike(pattern),
                    Profile.display_name.ilike(pattern),
                )
            )

        # Build the query
        stmt = (
            select(Post, Profile, rank_score.label("rank_score"))
            .join(Profile, Post.user_id == Profile.user_id)
            .join(User, Profile.user_id == User.id)
            .where(*filters)
            .where(Post.deleted_at.is_(None))
            .where(Post.status == ContentStatus.PUBLISHED)
            .where(Post.moderation_status == "approved")
            .where(User.status == AccountStatus.ACTIVE)
            .where(User.deleted_at.is_(None))
            .where(Profile.deleted_at.is_(None))
            .order_by(desc("rank_score"), Post.view_count.desc(), Post.id.desc())
            .offset(offset)
            .limit(limit)
        )

        if viewer_id is None:
            stmt = stmt.where(
                Profile.private_account.is_(False),
                Post.visibility == "public",
            )
        else:
            blocked_by_viewer = select(UserBlock.blocked_id).where(UserBlock.blocker_id == viewer_id)
            blocking_viewer = select(UserBlock.blocker_id).where(UserBlock.blocked_id == viewer_id)
            muted_by_viewer = select(UserMute.muted_id).where(UserMute.muter_id == viewer_id)
            stmt = stmt.where(
                ~Post.user_id.in_(blocked_by_viewer),
                ~Post.user_id.in_(blocking_viewer),
                ~Post.user_id.in_(muted_by_viewer),
                or_(
                    Post.user_id == viewer_id,
                    and_(
                        Profile.private_account.is_(False),
                        Post.visibility == "public",
                    ),
                    and_(
                        Post.user_id.in_(following_ids),
                        Post.visibility.in_(("public", "followers")),
                    ),
                ),
            )

        result = await self.db.execute(stmt)
        return [(post, profile, score) for post, profile, score in result.all()]


class CommentRepository(BaseRepository[Comment]):
    """Repository for Comment model database operations.

    Extends BaseRepository with common CRUD operations and adds
    comment-specific methods for threading and post associations.
    """

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        super().__init__(db, Comment)

    async def get_by_id(self, comment_id: uuid.UUID) -> Optional[Comment]:
        """
        Get comment by ID.

        Args:
            comment_id: Comment ID (UUID)

        Returns:
            Comment object or None if not found
        """
        result = await self.db.execute(
            select(Comment).where(
                Comment.id == comment_id,
                Comment.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_post_id(
        self,
        post_id: uuid.UUID,
        parent_id: Optional[uuid.UUID] = None,
        limit: int = 50,
    ) -> list[Comment]:
        """
        Get comments for a post, optionally filtered by parent for threading.

        Args:
            post_id: Post ID to get comments for
            parent_id: Optional parent ID for threaded comments
            limit: Maximum number of comments to return

        Returns:
            List of Comment objects
        """
        stmt = select(Comment).where(
            Comment.post_id == post_id,
            Comment.deleted_at.is_(None),
        )
        if parent_id is not None:
            stmt = stmt.where(Comment.parent_id == parent_id)
        else:
            # Top-level comments only (parent_id IS NULL)
            stmt = stmt.where(Comment.parent_id.is_(None))
        stmt = stmt.order_by(Comment.created_at.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_for_creator(
        self,
        creator_id: uuid.UUID,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """Count comments on a creator's posts, optionally scoped to a date range."""
        stmt = (
            select(func.count())
            .select_from(Comment)
            .join(Post, Comment.post_id == Post.id)
            .where(Post.user_id == creator_id, Comment.deleted_at.is_(None))
        )
        if start is not None:
            stmt = stmt.where(Comment.created_at >= start)
        if end is not None:
            stmt = stmt.where(Comment.created_at <= end)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def soft_delete(self, comment_id: uuid.UUID) -> bool:
        """
        Soft-delete a comment.

        Args:
            comment_id: Comment ID to soft delete

        Returns:
            True if comment was found and soft-deleted, False otherwise
        """
        comment = await self.get_by_id(comment_id)
        if not comment:
            return False
        comment.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
        return True


class MediaRepository(BaseRepository[Media]):
    """Repository for Media records attached to posts."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, Media)

    async def get_by_id(
        self, media_id: uuid.UUID, include_deleted: bool = False
    ) -> Optional[Media]:
        stmt = select(Media).where(Media.id == media_id)
        if not include_deleted:
            stmt = stmt.where(Media.deleted_at.is_(None))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_post_id(self, post_id: uuid.UUID) -> list[Media]:
        result = await self.db.execute(
            select(Media)
            .where(Media.post_id == post_id, Media.deleted_at.is_(None))
            .order_by(Media.created_at.asc())
        )
        return list(result.scalars().all())

    async def soft_delete(self, media: Media) -> None:
        media.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
