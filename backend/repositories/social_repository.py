"""
Repositories for ported legacy social features — follows, per-post
interactions (like/save/share/watch), comment likes, playlists, sounds,
and search history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.models.social import (
    CommentLike,
    Follow,
    Playlist,
    PostLike,
    PostSave,
    PostShare,
    PostWatch,
    SearchQuery,
    Sound,
    UserBlock,
    UserMute,
)
from repositories.base import BaseRepository


class FollowRepository(BaseRepository[Follow]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Follow)

    async def is_following(self, follower_id: uuid.UUID, following_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(Follow.id).where(
                Follow.follower_id == follower_id, Follow.following_id == following_id
            )
        )
        return result.first() is not None

    async def follow(self, follower_id: uuid.UUID, following_id: uuid.UUID) -> bool:
        """Create a follow relationship, returning whether this call created it."""
        result = await self.db.execute(
            insert(Follow)
            .values(follower_id=follower_id, following_id=following_id)
            .on_conflict_do_nothing(constraint="uq_follow_pair")
        )
        await self.db.flush()
        return result.rowcount == 1

    async def unfollow(self, follower_id: uuid.UUID, following_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(Follow).where(
                Follow.follower_id == follower_id, Follow.following_id == following_id
            )
        )
        await self.db.flush()

    async def get_following_ids(self, follower_id: uuid.UUID) -> list[uuid.UUID]:
        result = await self.db.execute(
            select(Follow.following_id).where(Follow.follower_id == follower_id)
        )
        return list(result.scalars().all())

    async def get_follower_ids(self, following_id: uuid.UUID) -> list[uuid.UUID]:
        result = await self.db.execute(
            select(Follow.follower_id).where(Follow.following_id == following_id)
        )
        return list(result.scalars().all())

    async def count_followers(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(Follow).where(Follow.following_id == user_id)
        )
        return result.scalar_one()

    async def count_following(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(Follow).where(Follow.follower_id == user_id)
        )
        return result.scalar_one()

    async def count_followers_since(
        self, user_id: uuid.UUID, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        """New followers gained within a date range — for follower-growth analytics."""
        stmt = select(func.count()).select_from(Follow).where(Follow.following_id == user_id)
        if start is not None:
            stmt = stmt.where(Follow.created_at >= start)
        if end is not None:
            stmt = stmt.where(Follow.created_at <= end)
        result = await self.db.execute(stmt)
        return result.scalar_one()


class FeedSafetyRepository:
    """Queries viewer-specific creator exclusions for feed generation."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_hidden_creator_ids(
        self, viewer_id: uuid.UUID, creator_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        if not creator_ids:
            return set()
        blocked = await self.db.execute(
            select(UserBlock.blocked_id).where(
                UserBlock.blocker_id == viewer_id,
                UserBlock.blocked_id.in_(creator_ids),
            )
        )
        blocking_viewer = await self.db.execute(
            select(UserBlock.blocker_id).where(
                UserBlock.blocked_id == viewer_id,
                UserBlock.blocker_id.in_(creator_ids),
            )
        )
        muted = await self.db.execute(
            select(UserMute.muted_id).where(
                UserMute.muter_id == viewer_id,
                UserMute.muted_id.in_(creator_ids),
            )
        )
        return set(blocked.scalars()) | set(blocking_viewer.scalars()) | set(muted.scalars())

    async def get_invitation_restricted_user_ids(
        self, initiator_id: uuid.UUID, target_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Return invitees blocked or muted in either direction."""
        if not target_ids:
            return set()

        blocked_by_initiator = await self.db.execute(
            select(UserBlock.blocked_id).where(
                UserBlock.blocker_id == initiator_id,
                UserBlock.blocked_id.in_(target_ids),
            )
        )
        blocking_initiator = await self.db.execute(
            select(UserBlock.blocker_id).where(
                UserBlock.blocked_id == initiator_id,
                UserBlock.blocker_id.in_(target_ids),
            )
        )
        muted_by_initiator = await self.db.execute(
            select(UserMute.muted_id).where(
                UserMute.muter_id == initiator_id,
                UserMute.muted_id.in_(target_ids),
            )
        )
        muting_initiator = await self.db.execute(
            select(UserMute.muter_id).where(
                UserMute.muted_id == initiator_id,
                UserMute.muter_id.in_(target_ids),
            )
        )
        return (
            set(blocked_by_initiator.scalars())
            | set(blocking_initiator.scalars())
            | set(muted_by_initiator.scalars())
            | set(muting_initiator.scalars())
        )


class PostInteractionRepository:
    """Likes/saves/shares/watches for posts — mirrors the legacy toggle semantics."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def has_liked(self, post_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(PostLike.id).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
        )
        return result.first() is not None

    async def has_saved(self, post_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(PostSave.id).where(PostSave.post_id == post_id, PostSave.user_id == user_id)
        )
        return result.first() is not None

    async def has_shared(self, post_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(PostShare.id).where(PostShare.post_id == post_id, PostShare.user_id == user_id)
        )
        return result.first() is not None

    async def liked_post_ids(self, user_id: uuid.UUID, post_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not post_ids:
            return set()
        result = await self.db.execute(
            select(PostLike.post_id).where(
                PostLike.user_id == user_id, PostLike.post_id.in_(post_ids)
            )
        )
        return set(result.scalars().all())

    async def saved_post_ids(self, user_id: uuid.UUID, post_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not post_ids:
            return set()
        result = await self.db.execute(
            select(PostSave.post_id).where(
                PostSave.user_id == user_id, PostSave.post_id.in_(post_ids)
            )
        )
        return set(result.scalars().all())

    async def shared_post_ids(self, user_id: uuid.UUID, post_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not post_ids:
            return set()
        result = await self.db.execute(
            select(PostShare.post_id).where(
                PostShare.user_id == user_id, PostShare.post_id.in_(post_ids)
            )
        )
        return set(result.scalars().all())

    async def toggle_like(self, post_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Toggle like, return the new liked state."""
        if await self.has_liked(post_id, user_id):
            await self.db.execute(
                delete(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
            )
            await self.db.flush()
            return False
        result = await self.db.execute(
            insert(PostLike)
            .values(post_id=post_id, user_id=user_id)
            .on_conflict_do_nothing(constraint="uq_post_like")
        )
        await self.db.flush()
        return result.rowcount > 0

    async def toggle_save(self, post_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        if await self.has_saved(post_id, user_id):
            await self.db.execute(
                delete(PostSave).where(PostSave.post_id == post_id, PostSave.user_id == user_id)
            )
            await self.db.flush()
            return False
        result = await self.db.execute(
            insert(PostSave)
            .values(post_id=post_id, user_id=user_id)
            .on_conflict_do_nothing(constraint="uq_post_save")
        )
        await self.db.flush()
        return result.rowcount > 0

    async def add_share(self, post_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Idempotent — repeated shares by the same user don't duplicate rows."""
        if await self.has_shared(post_id, user_id):
            return False
        self.db.add(PostShare(post_id=post_id, user_id=user_id))
        await self.db.flush()
        return True

    async def count_likes(self, post_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(PostLike).where(PostLike.post_id == post_id)
        )
        return result.scalar_one()

    async def count_saves(self, post_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(PostSave).where(PostSave.post_id == post_id)
        )
        return result.scalar_one()

    async def count_shares(self, post_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(PostShare).where(PostShare.post_id == post_id)
        )
        return result.scalar_one()

    async def count_views(self, post_id: uuid.UUID) -> int:
        """Distinct viewers, so concurrent watch events can't double-count."""
        result = await self.db.execute(
            select(func.count(func.distinct(PostWatch.user_id))).where(PostWatch.post_id == post_id)
        )
        return result.scalar_one()

    async def track_watch(
        self, post_id: uuid.UUID, user_id: uuid.UUID, watched_seconds: float, completed: bool
    ) -> PostWatch:
        """Always inserts a new row — one event per call, never merged/deduped."""
        existing = await self.db.execute(
            select(func.count()).select_from(PostWatch).where(
                PostWatch.post_id == post_id, PostWatch.user_id == user_id
            )
        )
        rewatched = existing.scalar_one() > 0
        watch = PostWatch(
            post_id=post_id,
            user_id=user_id,
            watched_seconds=watched_seconds,
            completed=completed,
            rewatched=rewatched,
        )
        self.db.add(watch)
        await self.db.flush()
        return watch


class CommentInteractionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def has_liked(self, comment_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(CommentLike.id).where(
                CommentLike.comment_id == comment_id, CommentLike.user_id == user_id
            )
        )
        return result.first() is not None

    async def liked_comment_ids(
        self, user_id: uuid.UUID, comment_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        if not comment_ids:
            return set()
        result = await self.db.execute(
            select(CommentLike.comment_id).where(
                CommentLike.user_id == user_id, CommentLike.comment_id.in_(comment_ids)
            )
        )
        return set(result.scalars().all())

    async def toggle_like(self, comment_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        if await self.has_liked(comment_id, user_id):
            await self.db.execute(
                delete(CommentLike).where(
                    CommentLike.comment_id == comment_id, CommentLike.user_id == user_id
                )
            )
            await self.db.flush()
            return False
        self.db.add(CommentLike(comment_id=comment_id, user_id=user_id))
        await self.db.flush()
        return True

    async def count_likes(self, comment_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(CommentLike).where(CommentLike.comment_id == comment_id)
        )
        return result.scalar_one()


class PlaylistRepository(BaseRepository[Playlist]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Playlist)

    async def get_by_profile_id(self, profile_id: uuid.UUID) -> list[Playlist]:
        result = await self.db.execute(
            select(Playlist).where(Playlist.profile_id == profile_id).order_by(Playlist.created_at.desc())
        )
        return list(result.scalars().all())


class SoundRepository(BaseRepository[Sound]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Sound)

    async def get_trending(self, genre: Optional[str] = None, limit: int = 20) -> list[Sound]:
        stmt = select(Sound)
        if genre:
            stmt = stmt.where(Sound.genre == genre)
        stmt = stmt.order_by(Sound.rank.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class SearchQueryRepository(BaseRepository[SearchQuery]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, SearchQuery)

    async def record(self, user_id: Optional[uuid.UUID], query_text: str) -> SearchQuery:
        import re

        normalized = re.sub(r"\s+", " ", query_text.strip().lower())[:200]
        entry = SearchQuery(user_id=user_id, query_text=query_text[:200], normalized_query=normalized)
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def get_recent(self, user_id: uuid.UUID, limit: int = 10) -> list[SearchQuery]:
        result = await self.db.execute(
            select(SearchQuery)
            .where(SearchQuery.user_id == user_id)
            .order_by(SearchQuery.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def clear(self, user_id: uuid.UUID) -> None:
        await self.db.execute(delete(SearchQuery).where(SearchQuery.user_id == user_id))
        await self.db.flush()

    async def remove_entry(self, user_id: uuid.UUID, query_text: str) -> None:
        import re

        normalized = re.sub(r"\s+", " ", query_text.strip().lower())[:200]
        await self.db.execute(
            delete(SearchQuery).where(
                SearchQuery.user_id == user_id, SearchQuery.normalized_query == normalized
            )
        )
        await self.db.flush()

    async def trending(self, limit: int = 10) -> list[str]:
        result = await self.db.execute(
            select(SearchQuery.normalized_query, func.count().label("cnt"))
            .group_by(SearchQuery.normalized_query)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [row[0] for row in result.all()]
