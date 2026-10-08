"""Focused tests for active media upload endpoints and storage validation."""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

from app.models.user import AccountStatus, User, UserRole
from api.graphql import _delete_account, _delete_post
from features.media.router import delete_media, get_media_content, upload_media
from services.media_storage import MediaStorage, MediaStorageError, StoredMedia


def make_user(role: UserRole = UserRole.USER) -> User:
    return User(
        id=uuid.uuid4(),
        email="alice@example.com",
        username="alice",
        hashed_password="hashed",
        role=role,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )


def make_upload(content: bytes, name: str = "upload.bin") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/media/posts/test",
            "raw_path": b"/media/posts/test",
            "query_string": b"",
            "headers": [],
            "client": ("192.0.2.1", 1234),
            "server": ("test", 80),
        }
    )


@pytest.mark.asyncio
async def test_storage_upload_validates_bytes_and_uses_secure_key():
    storage = MediaStorage()
    storage.client = SimpleNamespace(upload_file=Mock())
    storage.bucket = "test-bucket"
    user_id = uuid.uuid4()
    media_id = uuid.uuid4()

    result = await storage.upload(make_upload(b"\x89PNG\r\n\x1a\nimage"), user_id, media_id)

    assert result.media_type == "image/png"
    assert result.file_size_bytes == 13
    assert result.storage_key == f"uploads/{user_id}/{media_id}.png"
    storage.client.upload_file.assert_called_once()


@pytest.mark.asyncio
async def test_storage_rejects_spoofed_file_type():
    storage = MediaStorage()
    storage.client = SimpleNamespace(upload_file=Mock())

    with pytest.raises(HTTPException) as error:
        await storage.upload(make_upload(b"<script>bad</script>", "fake.png"), uuid.uuid4(), uuid.uuid4())

    assert error.value.status_code == 415
    storage.client.upload_file.assert_not_called()


@pytest.mark.asyncio
async def test_storage_rejects_oversized_image(monkeypatch):
    monkeypatch.setattr("services.media_storage.MAX_IMAGE_BYTES", 10)
    storage = MediaStorage()
    storage.client = SimpleNamespace(upload_file=Mock())

    with pytest.raises(HTTPException) as error:
        await storage.upload(make_upload(b"\xff\xd8\xff" + b"x" * 8), uuid.uuid4(), uuid.uuid4())

    assert error.value.status_code == 413
    storage.client.upload_file.assert_not_called()


@pytest.mark.asyncio
async def test_storage_failure_attempts_partial_object_cleanup():
    storage = MediaStorage()
    storage.client = SimpleNamespace(
        upload_file=Mock(side_effect=OSError("storage unavailable")),
        delete_object=Mock(),
    )
    storage.bucket = "test-bucket"
    upload = make_upload(b"\xff\xd8\xff")
    user_id, media_id = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(MediaStorageError, match="Unable to store media"):
        await storage.upload(upload, user_id, media_id)

    storage.client.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key=f"uploads/{user_id}/{media_id}.jpg"
    )
    assert upload.file.closed


@pytest.mark.asyncio
async def test_missing_storage_configuration_fails_without_upload(monkeypatch):
    from app.config import settings

    storage = MediaStorage()
    storage.client = None
    monkeypatch.setattr(settings, "aws_s3_bucket", None)
    monkeypatch.setattr(settings, "aws_region", None)
    monkeypatch.setattr(settings, "aws_access_key_id", None)
    monkeypatch.setattr(settings, "aws_secret_access_key", None)
    upload = make_upload(b"\xff\xd8\xff")

    with pytest.raises(MediaStorageError, match="not configured"):
        await storage.upload(upload, uuid.uuid4(), uuid.uuid4())

    assert upload.file.closed


@pytest.mark.asyncio
async def test_storage_delete_is_safe_to_retry():
    storage = MediaStorage()
    storage.client = SimpleNamespace(delete_object=Mock())
    storage.bucket = "test-bucket"

    await storage.delete("uploads/user/media.jpg")
    await storage.delete("uploads/user/media.jpg")

    assert storage.client.delete_object.call_count == 2


@pytest.mark.asyncio
async def test_upload_persists_media_for_owned_post(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=user.id)
    db = AsyncMock()
    created = []

    async def get_post(self, post_id):
        return post

    async def store_file(file, user_id, media_id):
        return StoredMedia("image/jpeg", 3, f"uploads/{user_id}/{media_id}.jpg")

    async def create_media(self, media):
        created.append(media)
        return media

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("features.media.router.media_storage.upload", store_file)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", create_media)

    response = await upload_media(make_request(), post.id, make_upload(b"\xff\xd8\xff"), user, db)

    assert response["post_id"] == str(post.id)
    assert response["media_type"] == "image/jpeg"
    assert created[0].user_id == user.id
    assert created[0].storage_key.endswith(".jpg")
    assert created[0].is_processed is False
    assert created[0].thumbnail_url is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_rejects_non_owner_before_storage_access(monkeypatch):
    owner = make_user()
    uploader = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=owner.id)
    db = AsyncMock()
    store_file = AsyncMock()

    async def get_post(self, post_id):
        return post

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("features.media.router.media_storage.upload", store_file)

    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), post.id, make_upload(b"\xff\xd8\xff"), uploader, db)

    assert error.value.status_code == 403
    store_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_removes_stored_object_when_persistence_fails(monkeypatch):
    user = make_user()
    post = SimpleNamespace(id=uuid.uuid4(), user_id=user.id)
    db = AsyncMock()
    delete_stored = AsyncMock()

    async def get_post(self, post_id):
        return post

    async def store_file(file, user_id, media_id):
        return StoredMedia("video/mp4", 12, "uploads/test.mp4")

    async def create_media(self, media):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("features.media.router.media_storage.upload", store_file)
    monkeypatch.setattr("features.media.router.media_storage.delete", delete_stored)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", create_media)

    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), post.id, make_upload(b"\x00"), user, db)

    assert error.value.status_code == 500
    db.rollback.assert_awaited_once()
    delete_stored.assert_awaited_once_with("uploads/test.mp4")


@pytest.mark.asyncio
async def test_private_media_requires_owner_or_admin(monkeypatch):
    owner = make_user()
    viewer = make_user()
    media = SimpleNamespace(id=uuid.uuid4(), user_id=owner.id, post_id=uuid.uuid4())
    post = SimpleNamespace(id=media.post_id, user_id=owner.id, visibility="private")
    db = AsyncMock()

    async def get_media(self, media_id):
        return media

    async def get_post(self, post_id):
        return post

    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", get_media)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)

    with pytest.raises(HTTPException) as error:
        await get_media_content(media.id, viewer, db)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_media_content_redirects_to_presigned_url(monkeypatch):
    user = make_user()
    media = SimpleNamespace(id=uuid.uuid4(), user_id=user.id, post_id=uuid.uuid4(), storage_key="uploads/a.jpg")
    post = SimpleNamespace(id=media.post_id, user_id=user.id, visibility="public")
    db = AsyncMock()

    async def get_media(self, media_id):
        return media

    async def get_post(self, post_id):
        return post

    async def download_url(storage_key):
        return "https://storage.example/uploads/a.jpg"

    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", get_media)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("features.media.router.media_storage.download_url", download_url)

    response = await get_media_content(media.id, user, db)

    assert response.status_code == 307
    assert response.headers["location"] == "https://storage.example/uploads/a.jpg"


@pytest.mark.asyncio
async def test_delete_rejects_non_owner(monkeypatch):
    owner = make_user()
    viewer = make_user()
    media = SimpleNamespace(id=uuid.uuid4(), user_id=owner.id, storage_key="uploads/a.jpg")
    db = AsyncMock()
    delete_stored = AsyncMock()

    async def get_media(self, media_id):
        return media

    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", get_media)
    monkeypatch.setattr("features.media.router.media_storage.delete", delete_stored)

    with pytest.raises(HTTPException) as error:
        await delete_media(media.id, viewer, db)

    assert error.value.status_code == 403
    delete_stored.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_media_cleans_storage_and_persists_record_deletion(monkeypatch):
    user = make_user()
    media = SimpleNamespace(
        id=uuid.uuid4(), user_id=user.id, storage_key="uploads/alice/a.jpg"
    )
    db = AsyncMock()
    delete_stored = AsyncMock()
    soft_delete = AsyncMock()

    async def get_media(self, media_id):
        return media

    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", get_media)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.soft_delete", soft_delete)
    monkeypatch.setattr("features.media.router.media_storage.delete", delete_stored)

    await delete_media(media.id, user, db)

    delete_stored.assert_awaited_once_with(media.storage_key)
    soft_delete.assert_awaited_once_with(media)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_storage_cleanup_keeps_media_record_for_retry(monkeypatch):
    user = make_user()
    media = SimpleNamespace(
        id=uuid.uuid4(), user_id=user.id, storage_key="uploads/alice/a.jpg"
    )
    db = AsyncMock()
    soft_delete = AsyncMock()
    delete_stored = AsyncMock(side_effect=MediaStorageError("Unable to delete media"))

    async def get_media(self, media_id):
        return media

    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", get_media)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.soft_delete", soft_delete)
    monkeypatch.setattr("features.media.router.media_storage.delete", delete_stored)

    with pytest.raises(HTTPException) as error:
        await delete_media(media.id, user, db)

    assert error.value.status_code == 503
    soft_delete.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_deletion_cleans_storage_objects(monkeypatch):
    from repositories.content_repository import MediaRepository, PostRepository

    user = make_user()
    post_id = uuid.uuid4()
    media = SimpleNamespace(storage_key="uploads/alice/post.jpg")
    db = AsyncMock()
    delete_stored = AsyncMock()

    async def get_post(self, requested_id):
        return SimpleNamespace(id=post_id, user_id=user.id)

    async def get_media(self, requested_post_id):
        return [media]

    async def soft_delete_media(self, media_item):
        return None

    async def soft_delete_post(self, requested_id):
        return True

    monkeypatch.setattr(PostRepository, "get_by_id", get_post)
    monkeypatch.setattr(PostRepository, "soft_delete", soft_delete_post)
    monkeypatch.setattr(MediaRepository, "get_by_post_id", get_media)
    monkeypatch.setattr(MediaRepository, "soft_delete", soft_delete_media)
    monkeypatch.setattr("services.media_lifecycle_service.media_storage.delete", delete_stored)
    ctx = SimpleNamespace(db=db, require_auth=lambda: user)

    assert await _delete_post(ctx, post_id)

    delete_stored.assert_awaited_once_with(media.storage_key)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_account_deletion_cleans_owned_storage_objects(monkeypatch):
    from repositories.content_repository import MediaRepository

    user = make_user()
    media = SimpleNamespace(storage_key="uploads/alice/account.jpg")
    db = AsyncMock()
    delete_stored = AsyncMock()

    async def get_media(self, user_id):
        return [media]

    async def soft_delete_media(self, media_item):
        return None

    monkeypatch.setattr(MediaRepository, "get_by_user_id", get_media)
    monkeypatch.setattr(MediaRepository, "soft_delete", soft_delete_media)
    monkeypatch.setattr("services.media_lifecycle_service.media_storage.delete", delete_stored)
    ctx = SimpleNamespace(db=db, require_auth=lambda: user)

    assert await _delete_account(ctx)

    delete_stored.assert_awaited_once_with(media.storage_key)
    db.delete.assert_awaited_once_with(user)
    db.commit.assert_awaited_once()