"""Storage cleanup shared by media, post, and account deletion paths."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Media
from repositories.content_repository import MediaRepository
from services.media_storage import media_storage


async def delete_media_records(db: AsyncSession, media_records: list[Media]) -> None:
    repository = MediaRepository(db)
    for media in media_records:
        await media_storage.delete(media.storage_key)
    for media in media_records:
        await repository.soft_delete(media)


async def delete_post_media(db: AsyncSession, post_id: uuid.UUID) -> None:
    media_records = await MediaRepository(db).get_by_post_id(post_id)
    await delete_media_records(db, media_records)


async def delete_user_media(db: AsyncSession, user_id: uuid.UUID) -> None:
    media_records = await MediaRepository(db).get_by_user_id(user_id)
    await delete_media_records(db, media_records)
