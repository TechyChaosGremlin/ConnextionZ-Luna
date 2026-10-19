"""Opt-in smoke test for an explicitly approved nonproduction storage target."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import uuid

import httpx
import pytest
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from app.config import settings
from services.media_storage import MediaStorage, MediaStorageError


RUN_ENV = "APPROVED_LIVE_STORAGE_SMOKE"
NONPRODUCTION_ENV = "APPROVED_LIVE_STORAGE_SMOKE_ENVIRONMENT"
BUCKET_ENV = "APPROVED_LIVE_STORAGE_SMOKE_BUCKET"
REGION_ENV = "APPROVED_LIVE_STORAGE_SMOKE_REGION"
ENDPOINT_ENV = "APPROVED_LIVE_STORAGE_SMOKE_ENDPOINT"
AWS_S3_ENDPOINT_SENTINEL = "aws-s3-default"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=="
)
MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


def _configured_secret() -> str:
    secret = settings.aws_secret_access_key
    return secret.get_secret_value().strip() if secret is not None else ""


def _require_approved_nonproduction_target() -> None:
    if os.environ.get(RUN_ENV, "").strip().lower() != "true":
        pytest.skip(f"set {RUN_ENV}=true only for an approved live-storage smoke")
    if os.environ.get(NONPRODUCTION_ENV, "").strip().lower() != "nonproduction":
        pytest.skip(f"{NONPRODUCTION_ENV} must explicitly be nonproduction")
    if settings.environment == "production":
        pytest.skip("live-storage smoke refuses to run with ENVIRONMENT=production")

    required_settings = {
        "AWS_S3_BUCKET": (settings.aws_s3_bucket or "").strip(),
        "AWS_REGION": (settings.aws_region or "").strip(),
        "AWS_ACCESS_KEY_ID": (settings.aws_access_key_id or "").strip(),
        "AWS_SECRET_ACCESS_KEY": _configured_secret(),
    }
    missing = [name for name, value in required_settings.items() if not value]
    if missing:
        pytest.skip("live-storage smoke configuration is missing: " + ", ".join(missing))

    expected_endpoint = (settings.aws_endpoint_url or "").strip() or AWS_S3_ENDPOINT_SENTINEL
    approvals = {
        BUCKET_ENV: required_settings["AWS_S3_BUCKET"],
        REGION_ENV: required_settings["AWS_REGION"],
        ENDPOINT_ENV: expected_endpoint,
    }
    missing_approvals = [name for name in approvals if not os.environ.get(name, "").strip()]
    if missing_approvals:
        pytest.skip(
            "live-storage smoke target approval is missing: " + ", ".join(missing_approvals)
        )
    mismatches = [
        name for name, configured_value in approvals.items()
        if os.environ[name].strip() != configured_value
    ]
    if mismatches:
        pytest.skip(
            "live-storage smoke target approval does not match configured target: "
            + ", ".join(mismatches)
        )


def _storage_client(storage: MediaStorage):
    try:
        client = storage._get_client()
    except MediaStorageError:
        raise AssertionError("live-storage smoke could not initialize storage") from None
    if storage.bucket is None:
        raise AssertionError("live-storage smoke storage bucket is unavailable")
    return client, storage.bucket


async def _object_exists(storage: MediaStorage, object_key: str) -> bool:
    client, bucket = _storage_client(storage)
    try:
        await asyncio.to_thread(client.head_object, Bucket=bucket, Key=object_key)
    except ClientError as exc:
        response = exc.response if isinstance(exc.response, dict) else {}
        code = str(response.get("Error", {}).get("Code", ""))
        if code in MISSING_CODES:
            return False
        raise AssertionError(
            "live-storage smoke object-status check returned an unexpected provider error"
        ) from None
    except (BotoCoreError, OSError):
        raise AssertionError("live-storage smoke object-status check failed") from None
    return True


async def _delete(storage: MediaStorage, object_key: str, *, final: bool) -> None:
    try:
        await storage.delete(object_key)
    except MediaStorageError:
        phase = "final cleanup" if final else "delete"
        raise AssertionError(f"live-storage smoke {phase} request failed") from None


@pytest.mark.asyncio
async def test_live_storage_smoke_round_trip_and_cleanup() -> None:
    _require_approved_nonproduction_target()

    storage = MediaStorage()
    user_id = uuid.uuid4()
    media_id = uuid.uuid4()
    # MediaStorage owns this key shape; random IDs plus HEAD-before-upload prevent reuse.
    object_key = f"uploads/{user_id}/{media_id}.png"
    safe_to_cleanup = False
    test_failure: Exception | None = None
    cleanup_failures: list[Exception] = []

    try:
        if await _object_exists(storage, object_key):
            raise AssertionError(
                "live-storage smoke refused to overwrite the generated object key"
            )
        safe_to_cleanup = True

        upload = UploadFile(filename="live-storage-smoke.png", file=io.BytesIO(PNG_BYTES))
        try:
            stored = await storage.upload(upload, user_id, media_id)
        except MediaStorageError:
            raise AssertionError("live-storage smoke upload failed") from None

        if stored.storage_key != object_key:
            raise AssertionError("live-storage smoke upload returned an unexpected object key")
        if stored.media_type != "image/png" or stored.file_size_bytes != len(PNG_BYTES):
            raise AssertionError("live-storage smoke upload metadata was unexpected")
        if not await _object_exists(storage, object_key):
            raise AssertionError("live-storage smoke upload was not remotely visible")

        try:
            presigned_url = await storage.download_url(object_key)
        except MediaStorageError:
            raise AssertionError("live-storage smoke could not generate retrieval URL") from None

        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
                response = await client.get(presigned_url)
        except httpx.HTTPError:
            raise AssertionError("live-storage smoke retrieval request failed") from None

        if response.status_code != 200:
            raise AssertionError("live-storage smoke retrieval did not return HTTP 200")
        if response.content != PNG_BYTES:
            raise AssertionError("live-storage smoke retrieval bytes did not match")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "image/png":
            raise AssertionError("live-storage smoke retrieval content type was not image/png")

        await _delete(storage, object_key, final=False)
        if await _object_exists(storage, object_key):
            raise AssertionError("live-storage smoke object remained after delete")
    except Exception as exc:
        test_failure = exc
    finally:
        if safe_to_cleanup:
            try:
                await _delete(storage, object_key, final=True)
            except AssertionError as exc:
                cleanup_failures.append(exc)
            try:
                if await _object_exists(storage, object_key):
                    cleanup_failures.append(
                        AssertionError(
                            "live-storage smoke final cleanup did not remove the object"
                        )
                    )
            except AssertionError as exc:
                cleanup_failures.append(exc)

    failures = ([test_failure] if test_failure is not None else []) + cleanup_failures
    if len(failures) == 1:
        raise failures[0] from None
    if failures:
        raise ExceptionGroup("live-storage smoke and cleanup failed", failures) from None
