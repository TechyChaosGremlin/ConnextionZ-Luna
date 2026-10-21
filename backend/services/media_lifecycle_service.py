"""Storage cleanup shared by media, post, and account deletion paths."""

from __future__ import annotations

import uuid
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Media, Post
from app.models.user import User, UserRole
from repositories.content_repository import MediaRepository, PostRepository
from services.media_storage import MediaStorageError, media_storage

logger = logging.getLogger(__name__)


async def delete_media_records(
    db: AsyncSession, media_records: list[Media], *, current_user: User | None = None,
) -> list[str]:
    """Detach under post locks; the caller must commit before object cleanup."""
    repository = MediaRepository(db)
    candidates: list[Media] = []
    requested = {media.id for media in media_records}
    for post_id in sorted({media.post_id for media in media_records}):
        post = await PostRepository(db).get_by_id(post_id, include_deleted=True, for_update=True)
        if post is None:
            raise HTTPException(status_code=409, detail="Post changed during deletion. Reload before retrying.")
        attached = await repository.get_by_post_id(post_id, include_deleted=True)
        selected = [media for media in attached if media.id in requested]
        for media in selected:
            if current_user and current_user.id != media.user_id and current_user.role != UserRole.ADMIN:
                raise HTTPException(status_code=403, detail="Not allowed to delete media")
            if await repository.has_other_post_references(media):
                raise HTTPException(status_code=409, detail="Another post still references this media. Deletion was refused.")
            if post.media_url == media.url and post.thumbnail and post.thumbnail != media.url:
                if not any(item.url == post.thumbnail for item in selected):
                    raise HTTPException(status_code=409, detail="Delete the video's thumbnail before deleting its video.")
        for media in selected:
            if post.media_url == media.url:
                post.media_url = None
            if post.thumbnail == media.url:
                post.thumbnail = None
            if media.deleted_at is None:
                await repository.soft_delete(media)
            candidates.append(media)
    await db.flush()
    cleanup = []
    for media in candidates:
        if not await repository.has_live_references(media.storage_key, media.url):
            cleanup.append(media.storage_key)
        else:
            logger.warning("Media object retained because another live reference exists")
    return list(dict.fromkeys(cleanup))


async def cleanup_media_objects(storage_keys: list[str]) -> None:
    for storage_key in storage_keys:
        try:
            await media_storage.delete(storage_key)
        except MediaStorageError as exc:
            logger.error("Detached media object cleanup failed")
            raise HTTPException(status_code=503, detail="Media was detached, but storage cleanup failed. Retry deletion.") from exc


async def delete_post_media(db: AsyncSession, post_id: uuid.UUID) -> list[str]:
    await PostRepository(db).get_by_id(post_id, include_deleted=True, for_update=True)
    media_records = await MediaRepository(db).get_by_post_id(post_id, include_deleted=True)
    return await delete_media_records(db, media_records)


async def delete_user_media(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    # Block new child posts via their FK while taking the existing post locks.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    await db.execute(
        select(Post.id).where(Post.user_id == user_id).order_by(Post.id).with_for_update()
    )
    media_records = await MediaRepository(db).get_for_user_deletion(user_id)
    return await delete_media_records(db, media_records)
