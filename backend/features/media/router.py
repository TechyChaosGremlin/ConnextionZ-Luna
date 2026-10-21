"""Protected media upload, retrieval, and deletion endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.models.analytics import EventType
from app.models.base import generate_uuidv7
from app.models.content import ContentType, Media, Post
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
from services.media_lifecycle_service import cleanup_media_objects, delete_media_records
from services.media_storage import MediaStorageError, media_storage

router = APIRouter(prefix="/media", tags=["media"])
upload_volume_limiter = ActionRateLimiter(UPLOAD_VOLUME_LIMITS)
logger = logging.getLogger(__name__)


def _can_manage(user: User, owner_id: uuid.UUID) -> bool:
    return user.id == owner_id or user.role == UserRole.ADMIN


async def _rollback_or_unconfirmed(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:
        logger.error("Media rollback unavailable; stored objects retained")
        raise HTTPException(
            status_code=503,
            detail="Database confirmation is unavailable. Retry to verify media state; stored objects were retained.",
        ) from exc


async def _get_media_or_404(db: AsyncSession, media_id: uuid.UUID) -> Media:
    media = await MediaRepository(db).get_by_id(media_id)
    if not media:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return media


async def _check_upload_state(
    db: AsyncSession, post: Post | None, user: User, role: str, repair_missing: bool,
    expected_primary_id: uuid.UUID | None,
) -> Post:
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if not _can_manage(user, post.user_id):
        raise HTTPException(status_code=403, detail="Not allowed to upload media")
    if repair_missing and post.content_type != ContentType.VIDEO:
        raise HTTPException(status_code=409, detail="This repair workflow is only for existing video posts")
    attached = await MediaRepository(db).get_by_post_id(post.id)
    if role == "primary":
        if attached or post.media_url or post.thumbnail:
            raise HTTPException(
                status_code=409, detail="Media already exists. Reload the post before uploading.",
            )
    elif post.thumbnail:
        raise HTTPException(
            status_code=409, detail="A thumbnail already exists. Reload the post before uploading.",
        )
    if role == "thumbnail":
        if (repair_missing and expected_primary_id is None) or not any(
            media.url == post.media_url and media.storage_key
            and (expected_primary_id is None or media.id == expected_primary_id)
            and (not repair_missing or media.media_type.startswith("video/"))
            for media in attached
        ):
            raise HTTPException(
                status_code=409,
                detail="The confirmed video no longer matches this post. Reload before repairing its thumbnail.",
            )
    return post


@router.post("/posts/{post_id}", status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    post_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    role: Annotated[Literal["primary", "thumbnail"], Form()] = "primary",
    repair_missing: Annotated[bool, Form()] = False,
    expected_primary_id: Annotated[uuid.UUID | None, Form()] = None,
    upload_id: Annotated[uuid.UUID | None, Form()] = None,
) -> dict[str, object]:
    repo = PostRepository(db)
    post = await repo.get_by_id(post_id)
    post = await _check_upload_state(db, post, current_user, role, repair_missing, expected_primary_id)
    initial_state = (post.user_id, post.content_type, post.media_url, post.thumbnail)

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

    media_id = upload_id or generate_uuidv7()
    owner_id = post.user_id if repair_missing else current_user.id
    try:
        # Retries may reuse a receipt ID, never an object key.
        stored = await media_storage.upload(file, owner_id, generate_uuidv7())
    except MediaStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media upload failed"
        ) from exc

    invalid_repair_type = repair_missing and (
        (role == "primary" and not stored.media_type.startswith("video/"))
        or (role == "thumbnail" and stored.media_type != "image/jpeg")
    )
    if invalid_repair_type or (role == "thumbnail" and not stored.media_type.startswith("image/")):
        try:
            await media_storage.delete(stored.storage_key)
        except MediaStorageError as exc:
            logger.error("Failed to clean up an invalid post media upload")
            detail = (
                "Invalid media upload cleanup failed"
                if invalid_repair_type else "Thumbnail upload cleanup failed"
            )
            raise HTTPException(status_code=503, detail=detail) from exc
        if invalid_repair_type:
            raise HTTPException(status_code=415, detail="Repair requires a video and a JPEG thumbnail")
        raise HTTPException(status_code=415, detail="A post thumbnail must be an image")

    try:
        post = await repo.get_by_id(post_id, for_update=True)
        post = await _check_upload_state(db, post, current_user, role, repair_missing, expected_primary_id)
        if (post.user_id, post.content_type, post.media_url, post.thumbnail) != initial_state:
            raise HTTPException(
                status_code=409, detail="Post media changed during upload. Reload before retrying.",
            )
        media = Media(
            id=media_id,
            post_id=post.id,
            user_id=owner_id,
            media_type=stored.media_type,
            url=f"/media/{media_id}/content",
            file_size_bytes=stored.file_size_bytes,
            storage_provider="s3",
            storage_key=stored.storage_key,
            is_processed=False,
        )
        await MediaRepository(db).create(media)
        if role == "thumbnail":
            post.thumbnail = media.url
        else:
            post.media_url = media.url
            post.content_type = (
                ContentType.VIDEO if media.media_type.startswith("video/") else ContentType.IMAGE
            )
            if post.content_type == ContentType.IMAGE:
                post.thumbnail = media.url
        if media.media_type.startswith("video/"):
            await AnalyticsEventService(db).track_event(
                event_type=EventType.VIDEO_UPLOADED,
                user=current_user,
                post=post,
                metadata={"file_size_bytes": media.file_size_bytes},
            )
    except HTTPException:
        await _rollback_or_unconfirmed(db)
        try:
            await media_storage.delete(stored.storage_key)
        except MediaStorageError as exc:
            logger.error("Failed to clean up media after an upload conflict")
            raise HTTPException(status_code=503, detail="Conflicting media upload cleanup failed") from exc
        raise
    except Exception:
        logger.error("Failed to persist uploaded post media")
        await _rollback_or_unconfirmed(db)
        try:
            await media_storage.delete(stored.storage_key)
        except MediaStorageError:
            logger.error("Failed to clean up media after a database upload failure")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Media upload failed",
        )

    try:
        await db.commit()
    except Exception:
        logger.error("Media commit outcome is uncertain; uploaded object retained")
        await _rollback_or_unconfirmed(db)
        raise HTTPException(
            status_code=503,
            detail=(
                "Upload confirmation is unavailable. Retry with the same upload identity to verify it."
                if upload_id else
                "Upload confirmation is unavailable. Reload the post to verify its media before retrying."
            ),
        )

    return _media_payload(media)


@router.get("/posts/{post_id}/uploads/{upload_id}")
async def get_upload_receipt(
    post_id: uuid.UUID,
    upload_id: uuid.UUID,
    role: Literal["primary", "thumbnail"],
    expected_primary_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    post = await PostRepository(db).get_by_id(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if not _can_manage(current_user, post.user_id):
        raise HTTPException(status_code=403, detail="Not allowed to verify this upload")
    media = await MediaRepository(db).get_by_id(upload_id, include_deleted=True)
    if media is None:
        return {"attachment": None}
    reference = post.media_url if role == "primary" else post.thumbnail
    if (
        media.post_id != post.id or media.deleted_at is not None or media.url != reference
        or not media.storage_key
        or (role == "thumbnail" and not media.media_type.startswith("image/"))
        or (role == "thumbnail" and expected_primary_id is not None
            and post.media_url != f"/media/{expected_primary_id}/content")
    ):
        raise HTTPException(status_code=409, detail="This upload is no longer attached. Reload before retrying.")
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
    payload = await _authorized_media_url(media_id, current_user, db)
    return RedirectResponse(str(payload["url"]), headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/url")
async def get_media_url(
    media_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    return JSONResponse(
        await _authorized_media_url(media_id, current_user, db),
        headers={"Cache-Control": "private, no-store"},
    )


async def _authorized_media_url(
    media_id: uuid.UUID, current_user: User, db: AsyncSession,
) -> dict[str, object]:
    media = await _get_media_or_404(db, media_id)
    post = await PostRepository(db).get_by_id(media.post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post.visibility != "public" and not _can_manage(current_user, media.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view media")
    try:
        return {
            "url": await media_storage.download_url(media.storage_key),
            "expires_in": 300,
            "media_type": media.media_type,
        }
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
    media = await MediaRepository(db).get_by_id(media_id, include_deleted=True)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    if not _can_manage(current_user, media.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to delete media")
    try:
        cleanup = await delete_media_records(db, [media], current_user=current_user)
        await db.commit()
    except HTTPException:
        await _rollback_or_unconfirmed(db)
        raise
    except Exception:
        await _rollback_or_unconfirmed(db)
        raise HTTPException(
            status_code=503,
            detail="Media deletion was not confirmed. Retry to verify; stored objects were retained.",
        )
    await cleanup_media_objects(cleanup)


def _media_payload(media: Media) -> dict[str, object]:
    return {
        "id": str(media.id),
        "post_id": str(media.post_id),
        "media_type": media.media_type,
        "url": media.url,
        "file_size_bytes": media.file_size_bytes,
    }