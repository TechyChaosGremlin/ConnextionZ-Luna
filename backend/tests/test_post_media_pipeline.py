"""Post media association, query contracts, and private object retrieval."""

from __future__ import annotations

import copy
import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
from features.media.router import delete_media, get_media_url, get_upload_receipt, router, upload_media
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

    async def get_post(self, post_id, **kwargs):
        return state.post if post_id == state.post.id else None

    async def get_posts(self, user_id, **kwargs):
        return [state.post]

    async def create_media(self, media):
        state.post.media.append(media)
        return media

    async def get_media(self, post_id, *, include_deleted=False):
        return [media for media in state.post.media if include_deleted or media.deleted_at is None]

    async def get_media_by_id(self, media_id, include_deleted=False):
        return next((
            media for media in state.post.media if media.id == media_id
            and (include_deleted or media.deleted_at is None)
        ), None)

    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_user_id", get_posts)
    monkeypatch.setattr("repositories.content_repository.PostRepository.update", AsyncMock())
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", create_media)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_post_id", get_media)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.get_by_id", get_media_by_id)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.has_live_references", AsyncMock(return_value=False))
    monkeypatch.setattr("repositories.content_repository.MediaRepository.has_other_post_references", AsyncMock(return_value=False))
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
async def test_failed_attachment_rolls_back_before_cleaning_staged_object(pipeline, monkeypatch):
    state, ctx = pipeline
    events = []
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/staged"),
    ))
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", AsyncMock(
        side_effect=RuntimeError("synthetic flush failure"),
    ))
    ctx.db.rollback.side_effect = lambda: events.append("rollback")
    cleanup = AsyncMock(side_effect=lambda key: events.append("cleanup"))
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db)
    assert error.value.status_code == 500
    assert events == ["rollback", "cleanup"]
    cleanup.assert_awaited_once_with("uploads/staged")
    ctx.db.commit.assert_not_awaited()
    assert not state.post.media_url


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_fails", [False, True])
async def test_attachment_failure_with_failed_rollback_or_cleanup_is_not_success(
    pipeline, monkeypatch, rollback_fails,
):
    state, ctx = pipeline
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/unattached"),
    ))
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", AsyncMock(
        side_effect=RuntimeError("synthetic persistence failure"),
    ))
    cleanup = AsyncMock(side_effect=MediaStorageError("synthetic cleanup failure"))
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    if rollback_fails:
        ctx.db.rollback.side_effect = ConnectionError("synthetic rollback failure")
    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db)
    assert error.value.status_code == (503 if rollback_fails else 500)
    if rollback_fails:
        cleanup.assert_not_awaited()
    else:
        cleanup.assert_awaited_once_with("uploads/unattached")
    assert not state.post.media_url
    assert not state.post.media
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [True, False])
@pytest.mark.parametrize("rollback_fails", [True, False])
async def test_unconfirmed_commit_never_deletes_staged_object(pipeline, monkeypatch, committed, rollback_fails):
    """Simulate both outcomes of a missing commit acknowledgement, not a DB commit."""
    state, ctx = pipeline
    upload_id = uuid.uuid4()
    durable = []
    async def commit():
        if committed:
            durable.append(state.post.media_url)
        raise ConnectionError("synthetic lost commit acknowledgement")
    ctx.db.commit.side_effect = commit
    if rollback_fails:
        ctx.db.rollback.side_effect = ConnectionError("synthetic rollback failure")
    cleanup = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/uncertain"),
    ))
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db,
            upload_id=upload_id,
        )
    assert error.value.status_code == 503
    assert "verify" in error.value.detail
    cleanup.assert_not_awaited()
    ctx.db.rollback.assert_awaited_once()
    if committed:
        receipt = await get_upload_receipt(
            state.post.id, upload_id, "primary", current_user=state.user, db=AsyncMock(),
        )
        assert receipt["url"] == durable[0]


@pytest.mark.asyncio
async def test_upload_receipt_requires_owner_and_current_association(pipeline, monkeypatch):
    state, ctx = pipeline
    upload_id = uuid.uuid4()
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/receipt"),
    ))
    assert await get_upload_receipt(
        state.post.id, upload_id, "primary", current_user=state.user, db=ctx.db,
    ) == {"attachment": None}
    await upload_media(
        make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db, upload_id=upload_id,
    )
    with pytest.raises(HTTPException) as denied:
        await get_upload_receipt(state.post.id, upload_id, "primary", current_user=make_user(), db=ctx.db)
    assert denied.value.status_code == 403
    state.post.media_url = "/media/another/content"
    with pytest.raises(HTTPException) as stale:
        await get_upload_receipt(state.post.id, upload_id, "primary", current_user=state.user, db=ctx.db)
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_multipart_upload_identity_and_authenticated_receipt(pipeline, monkeypatch):
    from app.dependencies import get_db_session
    from features.auth.middleware import get_current_active_user

    state, ctx = pipeline
    upload_id = uuid.uuid4()
    storage = AsyncMock(return_value=StoredMedia("video/mp4", 12, "uploads/receipt-staged"))
    monkeypatch.setattr("features.media.router.media_storage.upload", storage)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: ctx.db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/media/posts/{state.post.id}",
            data={"upload_id": str(upload_id), "role": "primary"},
            files={"file": ("video.mp4", b"video", "video/mp4")},
        )
        assert response.status_code == 201
        assert response.json()["id"] == str(upload_id)
        assert storage.call_args.args[2] != upload_id
        receipt = await client.get(f"/media/posts/{state.post.id}/uploads/{upload_id}?role=primary")
        assert receipt.status_code == 200
        assert receipt.json() == response.json()
        assert "storage_key" not in receipt.json()
        wrong_role = await client.get(f"/media/posts/{state.post.id}/uploads/{upload_id}?role=thumbnail")
        assert wrong_role.status_code == 409
        app.dependency_overrides[get_current_active_user] = lambda: make_user()
        denied = await client.get(f"/media/posts/{state.post.id}/uploads/{upload_id}?role=primary")
        assert denied.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["primary", "thumbnail"])
async def test_publish_checks_confirmed_attachment_ids_under_post_lock(pipeline, monkeypatch, role):
    state, ctx = pipeline
    primary_id, thumbnail_id = uuid.uuid4(), uuid.uuid4()
    state.post.media_url = f"/media/{primary_id}/content"
    state.post.thumbnail = f"/media/{thumbnail_id}/content"
    lookup = AsyncMock(return_value=state.post)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", lookup)
    before = state.post.caption, state.post.status, state.post.visibility
    with pytest.raises(ValueError, match="media changed"):
        await _update_post_legacy(ctx, state.post.id, UpdatePostInput(
            caption="Must not change", status="published",
            media_id=uuid.uuid4() if role == "primary" else primary_id,
            thumbnail_media_id=uuid.uuid4() if role == "thumbnail" else thumbnail_id,
        ))
    lookup.assert_awaited_once_with(state.post.id, for_update=True)
    assert (state.post.caption, state.post.status, state.post.visibility) == before
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion_wins", [True, False])
async def test_deletion_and_thumbnail_repair_share_post_lock(pipeline, monkeypatch, deletion_wins):
    """Controlled scheduling with a simulated lock, not PostgreSQL concurrency."""
    state, _ = pipeline
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/video"),
    ))
    await upload_media(make_request(), state.post.id, make_upload(b"video"), state.user, AsyncMock())
    primary = state.post.media[0]
    lock = asyncio.Lock()
    held = set()
    staged = asyncio.Event()
    release_storage = asyncio.Event()
    attaching = asyncio.Event()
    release_attachment = asyncio.Event()
    deletion_attempted = asyncio.Event()
    upload_db, delete_db = AsyncMock(), AsyncMock()

    async def get_post(self, post_id, *, for_update=False, include_deleted=False):
        if for_update:
            if self.db is delete_db:
                deletion_attempted.set()
            await lock.acquire()
            held.add(id(self.db))
        return state.post

    async def finish(db):
        if id(db) in held:
            held.remove(id(db))
            lock.release()

    async def finish_upload():
        await finish(upload_db)

    async def finish_delete():
        await finish(delete_db)

    upload_db.commit.side_effect = upload_db.rollback.side_effect = finish_upload
    delete_db.commit.side_effect = delete_db.rollback.side_effect = finish_delete

    async def upload(file, user_id, media_id):
        assert not held
        staged.set()
        await release_storage.wait()
        return StoredMedia("image/jpeg", 3, "uploads/poster")

    async def create(self, media):
        attaching.set()
        await release_attachment.wait()
        state.post.media.append(media)
        return media

    async def cleanup(key):
        assert not held

    cleanup_mock = AsyncMock(side_effect=cleanup)
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("repositories.content_repository.MediaRepository.create", create)
    monkeypatch.setattr("features.media.router.media_storage.upload", upload)
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup_mock)
    repair = asyncio.create_task(upload_media(
        make_request(), state.post.id, make_upload(b"jpg"), state.user, upload_db,
        role="thumbnail", repair_missing=True, expected_primary_id=primary.id,
    ))
    deletion = None
    try:
        await asyncio.wait_for(staged.wait(), 2)
        if deletion_wins:
            await delete_media(primary.id, state.user, delete_db)
            assert primary.deleted_at is not None
            assert state.post.media_url is None
            release_storage.set()
            with pytest.raises(HTTPException) as conflict:
                await asyncio.wait_for(repair, 2)
            assert conflict.value.status_code == 409
            assert not state.post.thumbnail
            assert cleanup_mock.await_count == 2
            upload_db.commit.assert_not_awaited()
        else:
            release_storage.set()
            await asyncio.wait_for(attaching.wait(), 2)
            deletion = asyncio.create_task(delete_media(primary.id, state.user, delete_db))
            await asyncio.wait_for(deletion_attempted.wait(), 2)
            assert not deletion.done()
            release_attachment.set()
            await asyncio.wait_for(repair, 2)
            with pytest.raises(HTTPException) as conflict:
                await asyncio.wait_for(deletion, 2)
            assert conflict.value.status_code == 409
            assert primary.deleted_at is None
            assert state.post.media_url == primary.url
            assert state.post.thumbnail
            cleanup_mock.assert_not_awaited()
            delete_db.commit.assert_not_awaited()
    finally:
        for task in (repair, deletion):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (repair, deletion) if task is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_repair_published_post_preserves_identity_metadata_and_relationships(pipeline, monkeypatch):
    state, ctx = pipeline
    state.post.status = ContentStatus.PUBLISHED
    state.post.caption = "Existing caption"
    state.post.visibility = "private"
    state.post.like_count = 17
    state.post.comment_count = 6
    state.post.view_count = 100
    before = copy.deepcopy(vars(state.post))
    comments = state.post.comments = [SimpleNamespace(id=uuid.uuid4())]
    stored = AsyncMock(side_effect=[
        StoredMedia("video/mp4", 12, "uploads/repaired.mp4"),
        StoredMedia("image/jpeg", 3, "uploads/repaired.jpg"),
    ])
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)

    primary = await upload_media(
        make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db,
        role="primary", repair_missing=True,
    )
    thumbnail = await upload_media(
        make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
        role="thumbnail", repair_missing=True, expected_primary_id=state.post.media[0].id,
    )
    assert state.post.media_url == primary["url"]
    assert state.post.thumbnail == thumbnail["url"]
    assert len(state.post.media) == 2
    assert all(media.post_id == state.post.id for media in state.post.media)
    for field, value in before.items():
        if field not in {"media", "media_url", "thumbnail", "content_type"}:
            assert getattr(state.post, field) == value
    assert state.post.comments is comments
    assert ctx.db.commit.await_count == 2


@pytest.mark.asyncio
async def test_repair_retries_cannot_overwrite_completed_uploads(pipeline, monkeypatch):
    state, ctx = pipeline
    stored = AsyncMock(return_value=StoredMedia("video/mp4", 12, "uploads/repaired.mp4"))
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)
    await upload_media(
        make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db,
        repair_missing=True,
    )
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db,
            repair_missing=True,
        )
    assert error.value.status_code == 409
    assert stored.await_count == 1
    stored.return_value = StoredMedia("image/jpeg", 3, "uploads/poster.jpg")
    await upload_media(
        make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
        role="thumbnail", repair_missing=True, expected_primary_id=state.post.media[0].id,
    )
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
            role="thumbnail", repair_missing=True, expected_primary_id=state.post.media[0].id,
        )
    assert error.value.status_code == 409
    assert stored.await_count == 2


@pytest.mark.asyncio
async def test_repair_thumbnail_failure_keeps_video_for_thumbnail_only_retry(pipeline, monkeypatch):
    state, ctx = pipeline
    stored = AsyncMock(side_effect=[
        StoredMedia("video/mp4", 12, "uploads/repaired.mp4"),
        MediaStorageError("failed"),
        StoredMedia("image/jpeg", 3, "uploads/poster.jpg"),
    ])
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)
    primary = await upload_media(
        make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db, repair_missing=True,
    )
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
            role="thumbnail", repair_missing=True, expected_primary_id=state.post.media[0].id,
        )
    assert error.value.status_code == 503
    assert state.post.media_url == primary["url"]
    assert len(state.post.media) == 1
    await upload_media(
        make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
        role="thumbnail", repair_missing=True, expected_primary_id=state.post.media[0].id,
    )
    assert len(state.post.media) == 2


@pytest.mark.asyncio
async def test_repair_rejects_non_owner_and_thumbnail_before_video(pipeline, monkeypatch):
    state, ctx = pipeline
    stored = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"video"), make_user(), ctx.db,
            repair_missing=True,
        )
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
            role="thumbnail", repair_missing=True,
        )
    assert error.value.status_code == 409
    stored.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_rejects_non_video_bytes_without_persisting(pipeline, monkeypatch):
    state, ctx = pipeline
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("image/jpeg", 3, "uploads/not-video.jpg"),
    ))
    cleanup = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
            repair_missing=True,
        )
    assert error.value.status_code == 415
    cleanup.assert_awaited_once()
    assert not state.post.media
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_rejects_non_jpeg_thumbnail(pipeline, monkeypatch):
    state, ctx = pipeline
    stored = AsyncMock(side_effect=[
        StoredMedia("video/mp4", 12, "uploads/repaired.mp4"),
        StoredMedia("image/png", 3, "uploads/not-jpeg.png"),
    ])
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)
    cleanup = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    await upload_media(
        make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db, repair_missing=True,
    )
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"png"), state.user, ctx.db,
            role="thumbnail", repair_missing=True, expected_primary_id=state.post.media[0].id,
        )
    assert error.value.status_code == 415
    assert len(state.post.media) == 1
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_repair_keeps_media_owned_by_post_author(pipeline, monkeypatch):
    from app.models.user import UserRole

    state, ctx = pipeline
    stored = AsyncMock(return_value=StoredMedia("video/mp4", 12, "uploads/repaired.mp4"))
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)
    await upload_media(
        make_request(), state.post.id, make_upload(b"video"), make_user(UserRole.ADMIN), ctx.db,
        repair_missing=True,
    )
    assert state.post.media[0].user_id == state.user.id
    assert stored.call_args.args[1] == state.user.id


@pytest.mark.asyncio
async def test_repair_multipart_flag_reaches_guard_before_storage(pipeline, monkeypatch):
    from app.dependencies import get_db_session
    from features.auth.middleware import get_current_active_user

    state, ctx = pipeline
    state.post.thumbnail = "/media/existing/content"
    stored = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", stored)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: ctx.db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/media/posts/{state.post.id}", data={"repair_missing": "true", "role": "primary"},
            files={"file": ("video.mp4", b"video", "video/mp4")},
        )
    assert response.status_code == 409
    stored.assert_not_awaited()


@pytest.mark.asyncio
async def test_repair_lookup_locks_post_and_refreshes_loaded_state():
    from sqlalchemy.dialects import postgresql
    from repositories.content_repository import PostRepository

    db = AsyncMock()
    db.execute.return_value = Mock()
    await PostRepository(db).get_by_id(uuid.uuid4(), for_update=True)
    statement = db.execute.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["primary", "thumbnail"])
@pytest.mark.parametrize("winner_repair,loser_repair", [
    (True, False), (False, True), (True, True), (False, False),
])
async def test_interleaved_uploads_preserve_first_attachment(
    pipeline, monkeypatch, role, winner_repair, loser_repair,
):
    """Exercise scheduling with a simulated row lock, not PostgreSQL concurrency."""
    state, _ = pipeline
    state.post.status = ContentStatus.PUBLISHED
    primary_id = uuid.uuid4()
    if role == "thumbnail":
        state.post.media_url = f"/media/{primary_id}/content"
        state.post.media = [SimpleNamespace(
            id=primary_id, url=state.post.media_url, storage_key="uploads/primary.mp4",
            media_type="video/mp4", deleted_at=None,
        )]
    winner_db, loser_db = AsyncMock(), AsyncMock()
    lock = asyncio.Lock()
    held = set()
    storage_entered = [asyncio.Event(), asyncio.Event()]
    storage_release = [asyncio.Event(), asyncio.Event()]
    storage_calls = []

    async def get_post(self, post_id, *, for_update=False):
        if for_update:
            await lock.acquire()
            held.add(id(self.db))
        return state.post

    async def finish(db):
        if id(db) in held:
            held.remove(id(db))
            lock.release()

    async def winner_finish():
        await finish(winner_db)

    async def loser_finish():
        await finish(loser_db)

    winner_db.commit.side_effect = winner_db.rollback.side_effect = winner_finish
    loser_db.commit.side_effect = loser_db.rollback.side_effect = loser_finish

    async def store(file, user_id, media_id):
        assert not held, "Storage I/O must run outside the simulated row lock"
        index = len(storage_calls)
        storage_calls.append(media_id)
        storage_entered[index].set()
        await storage_release[index].wait()
        return StoredMedia(
            "video/mp4" if role == "primary" else "image/jpeg",
            12, f"uploads/request-{index}",
        )

    cleanup = AsyncMock()
    upload_id = uuid.uuid4()
    monkeypatch.setattr("repositories.content_repository.PostRepository.get_by_id", get_post)
    monkeypatch.setattr("features.media.router.media_storage.upload", store)
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    winner = asyncio.create_task(upload_media(
        make_request(), state.post.id, make_upload(b"file"), state.user, winner_db,
        role=role, repair_missing=winner_repair,
        expected_primary_id=primary_id if role == "thumbnail" else None,
        upload_id=upload_id,
    ))
    loser = None
    try:
        await asyncio.wait_for(storage_entered[0].wait(), 2)
        loser = asyncio.create_task(upload_media(
            make_request(), state.post.id, make_upload(b"file"), state.user, loser_db,
            role=role, repair_missing=loser_repair,
            expected_primary_id=primary_id if role == "thumbnail" else None,
            upload_id=upload_id,
        ))
        await asyncio.wait_for(storage_entered[1].wait(), 2)
        storage_release[0].set()
        response = await asyncio.wait_for(winner, 2)
        storage_release[1].set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(loser, 2)
        assert error.value.status_code == 409
        assert (state.post.media_url if role == "primary" else state.post.thumbnail) == response["url"]
        assert len(state.post.media) == (1 if role == "primary" else 2)
        assert state.post.status == ContentStatus.PUBLISHED
        assert not held
        assert len(set(storage_calls)) == 2
        cleanup.assert_awaited_once_with("uploads/request-1")
        winner_db.commit.assert_awaited_once()
        loser_db.commit.assert_not_awaited()
        loser_db.rollback.assert_awaited_once()
    finally:
        for task in (winner, loser):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (winner, loser) if task is not None), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_matches", [False, True])
async def test_thumbnail_repair_binds_expected_video_before_and_after_storage(
    pipeline, monkeypatch, expected_matches,
):
    state, ctx = pipeline
    primary_id = uuid.uuid4()
    state.post.media_url = f"/media/{primary_id}/content"
    state.post.media = [SimpleNamespace(
        id=primary_id, url=state.post.media_url, storage_key="uploads/video.mp4",
        media_type="video/mp4", deleted_at=None,
    )]

    async def store(file, user_id, media_id):
        replacement_id = uuid.uuid4()
        state.post.media_url = f"/media/{replacement_id}/content"
        state.post.media = [SimpleNamespace(
            id=replacement_id, url=state.post.media_url, storage_key="uploads/replacement.mp4",
            media_type="video/mp4", deleted_at=None,
        )]
        return StoredMedia("image/jpeg", 3, "uploads/stale-poster.jpg")

    upload = AsyncMock(side_effect=store)
    cleanup = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", upload)
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db,
            role="thumbnail", repair_missing=True,
            expected_primary_id=primary_id if expected_matches else uuid.uuid4(),
        )
    assert error.value.status_code == 409
    assert not state.post.thumbnail
    assert len(state.post.media) == 1
    ctx.db.commit.assert_not_awaited()
    if expected_matches:
        upload.assert_awaited_once()
        cleanup.assert_awaited_once_with("uploads/stale-poster.jpg")
    else:
        upload.assert_not_awaited()
        cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_upload_cannot_replace_completed_repair(pipeline, monkeypatch):
    state, ctx = pipeline
    upload = AsyncMock(return_value=StoredMedia("video/mp4", 12, "uploads/repaired.mp4"))
    monkeypatch.setattr("features.media.router.media_storage.upload", upload)
    await upload_media(
        make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db, repair_missing=True,
    )
    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), state.post.id, make_upload(b"video"), state.user, ctx.db)
    assert error.value.status_code == 409
    upload.assert_awaited_once()
    assert len(state.post.media) == 1


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
    primary = SimpleNamespace(
        id=uuid.uuid4(), url="/media/primary/content", storage_key="uploads/primary.mp4",
        media_type="video/mp4", deleted_at=None,
    )
    state.post.media_url = primary.url
    state.post.media = [primary]
    cleanup = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", AsyncMock(
        return_value=StoredMedia("video/mp4", 12, "uploads/invalid-thumbnail.mp4")
    ))
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    with pytest.raises(HTTPException) as error:
        await upload_media(make_request(), state.post.id, make_upload(b"file"), state.user, ctx.db, role="thumbnail")
    assert error.value.status_code == 415
    assert state.post.media == [primary]
    assert not state.post.thumbnail
    cleanup.assert_awaited_once_with("uploads/invalid-thumbnail.mp4")
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_thumbnail_requires_attached_primary_before_storage(pipeline, monkeypatch):
    state, ctx = pipeline
    storage = AsyncMock()
    monkeypatch.setattr("features.media.router.media_storage.upload", storage)
    with pytest.raises(HTTPException) as error:
        await upload_media(
            make_request(), state.post.id, make_upload(b"jpg"), state.user, ctx.db, role="thumbnail",
        )
    assert error.value.status_code == 409
    storage.assert_not_awaited()
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_media_and_video_thumbnail_prevent_publication(pipeline):
    state, ctx = pipeline
    with pytest.raises(ValueError, match="Upload post media"):
        await _update_post_legacy(ctx, state.post.id, UpdatePostInput(status="published"))
    state.post.media_url = "/media/original/content"
    state.post.media = [SimpleNamespace(
        url=state.post.media_url, storage_key="uploads/movie.mp4", media_type="video/mp4", deleted_at=None,
    )]
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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/media/posts/{uuid.uuid4()}/uploads/{uuid.uuid4()}?role=primary")
    assert response.status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/media/posts/{uuid.uuid4()}", data={"repair_missing": "true", "role": "primary"},
            files={"file": ("video.mp4", b"video", "video/mp4")},
        )
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
