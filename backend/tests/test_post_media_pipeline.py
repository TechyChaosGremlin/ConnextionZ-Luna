"""Post media association, query contracts, and private object retrieval."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from api.graphql import (
    AppContext,
    FeedPageType,
    PostInput,
    ProfileSummaryType,
    UpdatePostInput,
    _create_post_legacy,
    _my_posts,
    _post_to_feed_item,
    _post_to_gql,
    _post_to_legacy_post,
    _profile_to_detail,
    _update_post_legacy,
    schema,
)
from app.models.content import ContentStatus, ContentType
from features.media.router import get_media_url, router, upload_media
from services.media_storage import MediaStorage, MediaStorageError, StoredMedia
from tests.test_following_feed import make_post, make_profile
from tests.test_media_upload import make_request, make_upload, make_user


@pytest.fixture
def pipeline(monkeypatch):
    user = make_user()
    post = make_post(user.id, uuid.uuid4(), created_at=datetime.now(timezone.utc))
    post.status = ContentStatus.DRAFT
    post.content_type = ContentType.VIDEO
    post.media = []
    post.title = post.body = post.tags = post.mentions = post.sound_track = None
    post.published_at = None
    post.updated_at = post.created_at
    profile = make_profile(user)
    profile.bio = profile.location = profile.website_url = profile.collab_status = ""
    profile.response_time = ""
    profile.online = False
    state = SimpleNamespace(user=user, post=post, profile=profile)
    db = AsyncMock()
    ctx = AppContext(db=db, current_user=user)

    async def get_post(self, post_id):
        return state.post if post_id == state.post.id else None

    async def get_posts(self, user_id, **kwargs):
        return [state.post]

    async def create_media(self, media):
        state.post.media.append(media)
        return media

    async def get_media(self, post_id):
        return state.post.media

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_user_id", get_posts)
    monkeypatch.setattr("repositories.content_repository.PostRepository.update", AsyncMock())
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", create_media)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_post_id", get_media)
    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_user_id", AsyncMock(return_value=profile))
    monkeypatch.setattr("repositories.social_repository.PlaylistRepository.get_by_profile_id", AsyncMock(return_value=[]))
    monkeypatch.setattr("api.graphql._visible_direct_read_posts", AsyncMock(side_effect=lambda ctx, posts: posts))
    monkeypatch.setattr("api.graphql._profile_to_summary", AsyncMock(return_value=ProfileSummaryType(
        id=profile.id, username=user.username, display_name=profile.display_name,
    )))
    monkeypatch.setattr("services.analytics_event_service.AnalyticsEventService.track_event", AsyncMock())
    for method in ("has_liked", "has_saved", "has_shared"):
        monkeypatch.setattr(
            f"repositories.social_repository.PostInteractionRepository.{method}",
            AsyncMock(return_value=False),
        )
    return state, ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image/png", "video/mp4"])
async def test_upload_associates_media_and_reloaded_queries_return_metadata(pipeline, monkeypatch, kind):
    state, ctx = pipeline

    async def store_file(file, user_id, media_id):
        return StoredMedia(kind, 12, f"uploads/{user_id}/{media_id}")

    monkeypatch.setattr("features.media.router.media_storage.upload", store_file)
    response = await upload_media(make_request(), state.post.id, make_upload(b"file"), state.user, ctx.db)
    assert state.post.media_url == response["url"]
    assert state.post.content_type == (ContentType.IMAGE if kind.startswith("image/") else ContentType.VIDEO)
    assert state.post.media[0].post_id == state.post.id
    assert state.post.media[0].storage_key
    assert state.post.status == ContentStatus.DRAFT
    assert not state.post.media_url.startswith(("blob:", "data:", "https:"))
    ctx.db.commit.assert_awaited_once()

    if kind.startswith("video/"):
        async def store_thumbnail(file, user_id, media_id):
            return StoredMedia("image/jpeg", 3, f"uploads/{user_id}/{media_id}.jpg")

        monkeypatch.setattr("features.media.router.media_storage.upload", store_thumbnail)
        await upload_media(make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db, role="thumbnail")
        assert state.post.media_url == response["url"]

    completed = await _update_post_legacy(ctx, state.post.id, UpdatePostInput(status="published"))
    assert completed.id == state.post.id
    assert completed.media_type == kind
    assert completed.thumbnail
    # A fresh object represents a later request, not the composer's local cache.
    state.post = copy.deepcopy(state.post)
    own = (await _my_posts(ctx))[0]
    profile = (await _profile_to_detail(ctx, state.profile)).posts[0]
    feed = await _post_to_feed_item(ctx, state.post)
    detail = _post_to_gql(state.post)
    for item in [own, profile, feed, detail]:
        assert item.id == state.post.id
        assert item.media_url == response["url"]
        assert item.media_type == kind
        assert item.thumbnail == state.post.thumbnail
    monkeypatch.setattr("api.graphql._feed", AsyncMock(return_value=FeedPageType(items=[feed])))
    result = await schema.execute(
        """
        query MediaAfterReload($id: UUID!) {
          myPosts { id thumbnail mediaUrl mediaType }
          me { posts { id thumbnail mediaUrl mediaType } }
          feed { items { id thumbnail mediaUrl mediaType } }
          post(id: $id) { id thumbnail mediaUrl mediaType }
        }
        """,
        variable_values={"id": str(state.post.id)},
        context_value=ctx,
    )
    assert not result.errors
    expected = {
        "id": str(state.post.id), "thumbnail": state.post.thumbnail,
        "mediaUrl": response["url"], "mediaType": kind,
    }
    assert result.data == {
        "myPosts": [expected], "me": {"posts": [expected]},
        "feed": {"items": [expected]}, "post": expected,
    }


@pytest.mark.asyncio
async def test_old_uploaded_media_is_read_without_rewriting_database_records(pipeline, monkeypatch):
    state, ctx = pipeline
    state.post.media = [
        SimpleNamespace(
            id=uuid.uuid4(), url="/media/original/content", media_type="video/mp4",
            storage_key="uploads/original.mp4", deleted_at=None,
        ),
        SimpleNamespace(
            id=uuid.uuid4(), url="/media/poster/content", media_type="image/jpeg",
            storage_key="uploads/poster.jpg", deleted_at=None,
        ),
    ]
    result = await _post_to_legacy_post(ctx, state.post)
    assert result.media_url == "/media/original/content"
    assert result.thumbnail == "/media/poster/content"
    assert result.media_type == "video/mp4"
    assert state.post.media_url is None
    assert state.post.thumbnail == ""
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_keeps_post_unpublished(pipeline, monkeypatch):
    state, ctx = pipeline
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(side_effect=MediaStorageError("failed")))
    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), state.post.id, make_upload(b"file"), state.user, ctx.db)
    assert error.value.status_code == 503
    assert state.post.status == ContentStatus.DRAFT
    assert state.post.media_url is None
    assert state.post.media == []
    ctx.db.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_thumbnail_rejects_video_without_associating_it(pipeline, monkeypatch):
    state, ctx = pipeline
    cleanup = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/invalid-thumbnail.mp4")
    ))
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), state.post.id, make_upload(b"file"), state.user, ctx.db, role="thumbnail")
    assert error.value.status_code == 415
    assert not state.post.media
    assert not state.post.thumbnail
    cleanup.assert_awaited_once_with("uploads/invalid-thumbnail.mp4")
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_media_and_video_thumbnail_prevent_publication(pipeline):
    state, ctx = pipeline
    with pytest.raises(ValueError, match="Upload post media"):
        await _update_post_legacy(ctx, state.post.id, UpdatePostInput(status="published"))
    state.post.media_url = "/media/original/content"
    state.post.media = [SimpleNamespace(url=state.post.media_url, storage_key="uploads/movie.mp4", media_type="video/mp4")]
    with pytest.raises(ValueError, match="thumbnail"):
        await _update_post_legacy(ctx, state.post.id, UpdatePostInput(status="published"))
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_cannot_publish_a_post_before_upload(pipeline):
    _, ctx = pipeline
    with pytest.raises(ValueError, match="Create a draft"):
        await _create_post_legacy(ctx, PostInput())
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_url_is_fresh_private_and_authorized(pipeline, monkeypatch):
    state, ctx = pipeline
    media = SimpleNamespace(
        id=uuid.uuid4(), post_id=state.post.id, user_id=state.user.id,
        media_type="image/png", storage_key="uploads/private.png",
    )
    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", AsyncMock(return_value=media))
    sign = AsyncMock(side_effect=["https://storage.example/first", "https://storage.example/second"])
    monkeypatch.setattr("features.media.router.media_storage.download_url", sign)
    first = await get_media_url(media.id, state.user, ctx.db)
    second = await get_media_url(media.id, state.user, ctx.db)
    assert json.loads(first.body)["url"] != json.loads(second.body)["url"]
    assert json.loads(first.body)["expires_in"] == 300
    assert first.headers["cache-control"] == "private, no-store"
    assert sign.await_count == 2
    state.post.visibility = "private"
    with pytest.raises(HTTPException) as error:
        await get_media_url(media.id, make_user(), ctx.db)
    assert error.value.status_code == 403
    assert sign.await_count == 2


@pytest.mark.asyncio
async def test_media_url_storage_failure_is_not_success(pipeline, monkeypatch):
    state, ctx = pipeline
    media = SimpleNamespace(
        id=uuid.uuid4(), post_id=state.post.id, user_id=state.user.id,
        media_type="image/png", storage_key="uploads/private.png",
    )
    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", AsyncMock(return_value=media))
    monkeypatch.setattr("features.media.router.media_storage.download_url", AsyncMock(side_effect=MediaStorageError("failed")))
    with pytest.raises(HTTPException) as error:
        await get_media_url(media.id, state.user, ctx.db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_media_url_endpoint_requires_authentication():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/media/{uuid.uuid4()}/url")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_storage_upload_supports_video_bytes():
    from unittest.mock import Mock

    storage = MediaStorage()
    storage.bucket = "test-bucket"
    storage.client = SimpleNamespace(upload_file=Mock())
    result = await storage.upload(
        make_upload(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 12, "movie.mp4"),
        uuid.uuid4(), uuid.uuid4(),
    )
    assert result.media_type == "video/mp4"
    assert result.storage_key.endswith(".mp4")
    assert storage.client.upload_file.call_args.kwargs["ExtraArgs"] == {"ContentType": "video/mp4"}
