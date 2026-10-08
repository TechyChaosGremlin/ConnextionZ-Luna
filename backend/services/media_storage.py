"""S3 media storage with streamed upload validation."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import boto3
from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status

from app.config import settings

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/ogg"}
MEDIA_TYPES = IMAGE_TYPES | VIDEO_TYPES
MAX_IMAGE_BYTES = settings.media_max_image_bytes
MAX_VIDEO_BYTES = settings.media_max_video_bytes
SIGNATURE_BYTES = 64 * 1024
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/ogg": ".ogv",
}


class MediaStorageError(Exception):
    """Raised when object storage cannot complete an operation."""


@dataclass(frozen=True)
class StoredMedia:
    media_type: str
    file_size_bytes: int
    storage_key: str


def detect_media_type(header: bytes) -> str | None:
    """Identify supported media from bytes rather than request headers."""
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in header:
        return "video/webm"
    if header.startswith(b"OggS") and b"theora" in header:
        return "video/ogg"
    if header[4:8] == b"ftyp":
        major_brand = header[8:12]
        if major_brand == b"qt  ":
            return "video/quicktime"
        if major_brand in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"MSNV"}:
            return "video/mp4"
    return None


class MediaStorage:
    """Store private media objects in the configured S3-compatible bucket."""

    def __init__(self) -> None:
        self.bucket: str | None = None
        self.client = None

    def _get_client(self):
        if self.client is not None:
            if self.bucket is None:
                raise MediaStorageError("Media storage is not configured")
            return self.client
        bucket = settings.aws_s3_bucket
        region = settings.aws_region
        access_key_id = settings.aws_access_key_id
        secret_access_key = settings.aws_secret_access_key
        if (
            not bucket
            or not region
            or not access_key_id
            or not secret_access_key
            or not secret_access_key.get_secret_value()
        ):
            raise MediaStorageError("Media storage is not configured")
        try:
            self.client = boto3.client(
                "s3",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key.get_secret_value(),
                region_name=region,
                endpoint_url=settings.aws_endpoint_url or None,
            )
        except (BotoCoreError, ClientError) as exc:
            raise MediaStorageError("Unable to initialize media storage") from exc
        self.bucket = bucket
        return self.client

    async def upload(
        self, upload: UploadFile, user_id: uuid.UUID, media_id: uuid.UUID
    ) -> StoredMedia:
        temporary_path: Path | None = None
        size = 0
        header = bytearray()
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temporary_file:
                temporary_path = Path(temporary_file.name)
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="Uploaded file is too large",
                        )
                    if len(header) < SIGNATURE_BYTES:
                        header.extend(chunk[: SIGNATURE_BYTES - len(header)])
                    temporary_file.write(chunk)

            media_type = detect_media_type(bytes(header))
            if media_type not in MEDIA_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Unsupported or invalid media file",
                )
            if media_type in IMAGE_TYPES and size > MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Uploaded image is too large",
                )

            client = self._get_client()
            bucket = self.bucket
            if bucket is None:
                raise MediaStorageError("Media storage is not configured")
            storage_key = f"uploads/{user_id}/{media_id}{CONTENT_TYPE_EXTENSIONS[media_type]}"
            try:
                await asyncio.to_thread(
                    client.upload_file,
                    str(temporary_path),
                    bucket,
                    storage_key,
                    ExtraArgs={"ContentType": media_type},
                )
            except (BotoCoreError, ClientError, OSError) as exc:
                try:
                    await asyncio.to_thread(
                        client.delete_object, Bucket=bucket, Key=storage_key
                    )
                except (Boto3Error, BotoCoreError, ClientError, OSError):
                    logging.getLogger(__name__).warning(
                        "Failed to clean up media after an upload error"
                    )
                raise MediaStorageError("Unable to store media") from exc
            return StoredMedia(
                media_type=media_type,
                file_size_bytes=size,
                storage_key=storage_key,
            )
        except HTTPException:
            raise
        except (Boto3Error, BotoCoreError, ClientError, OSError) as exc:
            raise MediaStorageError("Unable to store media") from exc
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            await upload.close()

    async def delete(self, storage_key: str) -> None:
        try:
            client = self._get_client()
            bucket = self.bucket
            if bucket is None:
                raise MediaStorageError("Media storage is not configured")
            await asyncio.to_thread(
                client.delete_object, Bucket=bucket, Key=storage_key
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise MediaStorageError("Unable to delete media") from exc

    async def download_url(self, storage_key: str) -> str:
        try:
            client = self._get_client()
            bucket = self.bucket
            if bucket is None:
                raise MediaStorageError("Media storage is not configured")
            return await asyncio.to_thread(
                client.generate_presigned_url,
                "get_object",
                Params={"Bucket": bucket, "Key": storage_key},
                ExpiresIn=300,
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise MediaStorageError("Unable to access media") from exc


media_storage = MediaStorage()