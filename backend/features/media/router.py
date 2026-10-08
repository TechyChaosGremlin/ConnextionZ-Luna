"""Protected media upload, retrieval, and deletion endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.models.analytics import EventType
from app.models.base import generate_uuidv7
from app.models.content import Media
from app.models.user import User, UserRole
from app.rate_limits import (
    UPLOAD_VOLUME_ACTION,
    UPLOAD_VOLUME_LIMITS,
    ActionRateLimitExceeded,
    ActionRateLimiter,
    client_identity,
)
from features.auth.middleware import get_current_active_user
from repositories.content_repository import MediaRepository, PostRepository
from services.analytics_event_service import AnalyticsEventService
from services.media_lifecycle_service import delete_media_records
from services.media_storage import MediaStorageError, media_storage

router = APIRouter(prefix="/media", tags=["media"])
upload_volume_limiter = ActionRateLimiter(UPLOAD_VOLUME_LIMITS)
logger = logging.getLogger(__name__)


def _can_manage(user: User, owner_id: uuid.UUID) -> bool:
    return user.id == owner_id or user.role == UserRole.ADMIN


async def _get_media_or_404(db: AsyncSession, media_id: uuid.UUID) -> Media:
    media = await MediaRepository(db).get_by_id(media_id)
    if not media:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return media


@router.post("/posts/{post_id}", status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    post_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    post = await PostRepository(db).get_by_id(post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if not _can_manage(current_user, post.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to upload media")

    try:
        upload_volume_limiter.consume(
            client_identity(
                current_user.id if current_user is not None else None,
                request.client.host if request.client else "unknown",
            ),
            {UPLOAD_VOLUME_ACTION: _uploaded_file_size(file)},
        )
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    media_id = generate_uuidv7()
    try:
        stored = await media_storage.upload(file, current_user.id, media_id)
    except MediaStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media upload failed"
        ) from exc

    media = Media(
        id=media_id,
        post_id=post.id,
        user_id=current_user.id,
        media_type=stored.media_type,
        url=f"/media/{media_id}/content",
        file_size_bytes=stored.file_size_bytes,
        storage_provider="s3",
        storage_key=stored.storage_key,
        is_processed=False,
    )
    try:
        await MediaRepository(db).create(media)
        if media.media_type.startswith("video/"):
            await AnalyticsEventService(db).track_event(
                event_type=EventType.VIDEO_UPLOADED,
                user=current_user,
                post=post,
                metadata={"file_size_bytes": media.file_size_bytes},
            )
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            await media_storage.delete(stored.storage_key)
        except MediaStorageError:
            logger.error("Failed to clean up media after a database upload failure")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Media upload failed",
        )

    return _media_payload(media)


def _uploaded_file_size(file: UploadFile) -> int:
    if file.size is not None:
        return max(file.size, 1)
    position = file.file.tell()
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(position)
    return max(size, 1)


@router.get("/posts/{post_id}")
async def list_post_media(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, object]]:
    post = await PostRepository(db).get_by_id(post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post.visibility != "public" and not _can_manage(current_user, post.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view media")
    return [_media_payload(media) for media in await MediaRepository(db).get_by_post_id(post.id)]


@router.get("/{media_id}/content")
async def get_media_content(
    media_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    media = await _get_media_or_404(db, media_id)
    post = await PostRepository(db).get_by_id(media.post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post.visibility != "public" and not _can_manage(current_user, media.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view media")
    try:
        return RedirectResponse(await media_storage.download_url(media.storage_key))
    except MediaStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media is unavailable"
        ) from exc


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    media_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    media = await _get_media_or_404(db, media_id)
    if not _can_manage(current_user, media.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to delete media")
    try:
        await delete_media_records(db, [media])
    except MediaStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media deletion failed"
        ) from exc
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Media deletion failed",
        )


def _media_payload(media: Media) -> dict[str, object]:
    return {
        "id": str(media.id),
        "post_id": str(media.post_id),
        "media_type": media.media_type,
        "url": media.url,
        "file_size_bytes": media.file_size_bytes,
    }