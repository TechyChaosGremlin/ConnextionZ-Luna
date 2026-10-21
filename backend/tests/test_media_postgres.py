"""Opt-in media races on a newly provisioned local PostgreSQL cluster only."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.analytics import AnalyticsEvent
from app.models.category import Category, profile_categories
from app.models.content import Comment, ContentStatus, ContentType, Media, Post
from app.models.user import Profile, Session, User
from features.media.router import delete_media, get_upload_receipt, upload_media
from repositories.content_repository import MediaRepository, PostRepository
from services.media_storage import StoredMedia, media_storage
from tests.test_media_upload import make_request, make_upload, make_user

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def local_cluster():
    if os.environ.get("MEDIA_TEST_LOCAL_POSTGRES") != "1":
        pytest.skip("Set MEDIA_TEST_LOCAL_POSTGRES=1 to provision a fresh local PostgreSQL cluster")
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.fail("Local PostgreSQL initdb and pg_ctl are required; no existing database fallback")
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    with tempfile.TemporaryDirectory(prefix="connextionz-media-pg-") as directory:
        root = Path(directory)
        data, logfile, password_file = root / "data", root / "postgres.log", root / "password"
        password = secrets.token_urlsafe(32)
        password_file.write_text(password, encoding="ascii")
        initialized = subprocess.run(
            [initdb, "-D", str(data), "-U", "media_test", "--auth=scram-sha-256",
             f"--pwfile={password_file}", "--encoding=UTF8", "--locale=C"],
            env=environment, capture_output=True, timeout=60,
        )
        password_file.unlink()
        if initialized.returncode:
            pytest.fail("Fresh local PostgreSQL initialization failed; no database was contacted")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        try:
            started = subprocess.run(
                [pg_ctl, "-D", str(data), "-l", str(logfile), "-w", "-t", "30",
                 "-o", f"-h 127.0.0.1 -p {port}", "start"],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=40,
            )
            if started.returncode:
                pytest.fail("Fresh local PostgreSQL startup failed; no existing database fallback")
            yield URL.create(
                "postgresql+asyncpg", username="media_test", password=password,
                host="127.0.0.1", port=port, database="postgres",
            )
        finally:
            stopped = subprocess.run(
                [pg_ctl, "-D", str(data), "-w", "-t", "30", "-m", "immediate", "stop"],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=40,
            )
            if stopped.returncode:
                pytest.fail("Disposable PostgreSQL shutdown failed; inspect the test process before retrying")


@pytest_asyncio.fixture
async def store(local_cluster):
    schema = f"media_race_{uuid.uuid4().hex}"
    admin = create_async_engine(local_cluster, hide_parameters=True)
    async with admin.begin() as connection:
        # Verify the just-created cluster, not application/environment settings.
        assert await connection.scalar(text("SELECT current_user")) == "media_test"
        assert await connection.scalar(text("SELECT host(inet_server_addr())")) == "127.0.0.1"
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        local_cluster, hide_parameters=True,
        connect_args={"server_settings": {
            "search_path": schema, "statement_timeout": "10000", "lock_timeout": "8000",
        }},
    )
    metadata = MetaData()
    for table in (
        User.__table__, Profile.__table__, Session.__table__, Category.__table__,
        profile_categories, Post.__table__, Comment.__table__, Media.__table__,
        AnalyticsEvent.__table__,
    ):
        assert isinstance(table, Table)
        table.to_metadata(metadata)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            assert await connection.scalar(text("SHOW transaction_isolation")) == "read committed"
        yield SimpleNamespace(sessions=sessions, engine=engine)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def media_case(store, monkeypatch):
    user = make_user()
    post = Post(
        user_id=user.id, content_type=ContentType.VIDEO, status=ContentStatus.PUBLISHED,
        caption="Preserve this caption", visibility="private", like_count=17,
        comment_count=1, media=[], comments=[], thumbnail=None, media_url=None,
    )
    async with store.sessions() as db:
        db.add(user)
        await db.flush()
        db.add(post)
        await db.flush()
        comment = Comment(user_id=user.id, post_id=post.id, body="Preserve this comment")
        db.add(comment)
        await db.commit()
    objects: dict[str, str] = {}
    deleted: list[str] = []

    async def upload(file, owner_id, object_id):
        assert owner_id == user.id
        kind = "image/jpeg" if file.filename == "poster.jpg" else "video/mp4"
        key = f"synthetic/{object_id}"
        objects[key] = kind
        return StoredMedia(kind, 12, key)

    async def cleanup(key):
        # Independent database observation proves cleanup follows rollback/commit.
        async with store.sessions() as db:
            assert not await db.scalar(select(Media.id).where(
                Media.storage_key == key, Media.deleted_at.is_(None),
            ).limit(1))
        deleted.append(key)
        objects.pop(key, None)

    monkeypatch.setattr("features.media.router.media_storage.upload", upload)
    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    return SimpleNamespace(
        user=user, post_id=post.id, comment_id=comment.id, objects=objects,
        deleted=deleted, upload=upload,
    )


async def attach(
    store, case, db=None, *, role: Literal["primary", "thumbnail"] = "primary",
    repair=True, expected=None, upload_id=None,
):
    async def send(session):
        return await upload_media(
            make_request(), case.post_id,
            make_upload(b"synthetic", "poster.jpg" if role == "thumbnail" else "video.mp4"),
            case.user, session, role=role, repair_missing=repair,
            expected_primary_id=expected, upload_id=upload_id,
        )
    if db is not None:
        return await send(db)
    async with store.sessions() as session:
        return await send(session)


async def assert_preserved(store, case):
    async with store.sessions() as db:
        post = await PostRepository(db).get_by_id(case.post_id)
        assert post is not None
        assert (post.caption, post.visibility, post.status, post.like_count, post.comment_count) == (
            "Preserve this caption", "private", ContentStatus.PUBLISHED, 17, 1,
        )
        assert [comment.id for comment in post.comments] == [case.comment_id]
        attached = await MediaRepository(db).get_by_post_id(post.id)
        for reference in (post.media_url, post.thumbnail):
            if reference:
                assert any(media.url == reference for media in attached)
        for media in attached:
            assert media.storage_key in case.objects
        return post, attached


async def wait_for_blockers(store, pids, expected_count):
    async def poll():
        while True:
            async with store.sessions() as observer:
                blocked = [
                    bool(await observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}))
                    for pid in pids
                ]
            if sum(blocked) >= expected_count:
                return
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), timeout=5)


async def finish_tasks(*tasks):
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["primary", "thumbnail"])
@pytest.mark.parametrize("repairs", [(True, True), (True, False), (False, True), (False, False)])
async def test_two_uploads_serialize_on_real_post_lock(store, media_case, monkeypatch, role, repairs):
    case = media_case
    primary = await attach(store, case) if role == "thumbnail" else None
    primary_id = uuid.UUID(str(primary["id"])) if primary else None
    staged = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    attempts = []

    async def upload(*args):
        result = await case.upload(*args)
        index = len(attempts)
        attempts.append(result.storage_key)
        staged[index].set()
        await release.wait()
        return result

    monkeypatch.setattr("features.media.router.media_storage.upload", upload)
    tasks = []
    async with store.sessions() as first, store.sessions() as second, store.sessions() as blocker:
        pids = [await db.scalar(text("SELECT pg_backend_pid()")) for db in (first, second)]
        assert pids[0] != pids[1]
        try:
            for db, repair in zip((first, second), repairs):
                tasks.append(asyncio.create_task(attach(
                    store, case, db, role=role, repair=repair, expected=primary_id,
                )))
                await asyncio.wait_for(staged[len(tasks) - 1].wait(), 5)
            await PostRepository(blocker).get_by_id(case.post_id, for_update=True)
            release.set()
            await wait_for_blockers(store, pids, 2)
            await blocker.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
            successes = [result for result in results if isinstance(result, dict)]
            conflicts = [result for result in results if isinstance(result, HTTPException)]
            assert len(successes) == len(conflicts) == 1
            assert conflicts[0].status_code == 409
            post, attached = await assert_preserved(store, case)
            assert (post.media_url if role == "primary" else post.thumbnail) == successes[0]["url"]
            assert len(attached) == (1 if role == "primary" else 2)
            assert len(case.deleted) == 1
            assert case.deleted[0] in attempts
        finally:
            await blocker.rollback()
            await finish_tasks(*tasks)


class PausedCommitSession(AsyncSession):
    ready: asyncio.Event
    release: asyncio.Event

    async def commit(self):
        self.ready.set()
        await self.release.wait()
        await super().commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion_wins", [True, False])
async def test_thumbnail_and_primary_deletion_actual_lock_order(store, media_case, monkeypatch, deletion_wins):
    case = media_case
    primary = await attach(store, case)
    primary_id = uuid.UUID(str(primary["id"]))
    staged, release_storage = asyncio.Event(), asyncio.Event()

    async def upload(*args):
        stored = await case.upload(*args)
        staged.set()
        await release_storage.wait()
        return stored

    monkeypatch.setattr("features.media.router.media_storage.upload", upload)
    repair_db = PausedCommitSession(bind=store.engine, expire_on_commit=False, autoflush=False)
    repair_db.ready, repair_db.release = asyncio.Event(), asyncio.Event()
    repair, deletion = None, None
    async with repair_db, store.sessions() as delete_db:
        delete_pid = await delete_db.scalar(text("SELECT pg_backend_pid()"))
        try:
            repair = asyncio.create_task(attach(
                store, case, repair_db, role="thumbnail", expected=primary_id,
            ))
            await asyncio.wait_for(staged.wait(), 5)
            if deletion_wins:
                await delete_media(primary_id, case.user, delete_db)
                release_storage.set()
                with pytest.raises(HTTPException) as conflict:
                    await asyncio.wait_for(repair, 5)
                assert conflict.value.status_code == 409
                post, attached = await assert_preserved(store, case)
                assert not post.media_url and not post.thumbnail and not attached
                assert len(case.deleted) == 2
            else:
                release_storage.set()
                await asyncio.wait_for(repair_db.ready.wait(), 5)
                deletion = asyncio.create_task(delete_media(primary_id, case.user, delete_db))
                await wait_for_blockers(store, [delete_pid], 1)
                repair_db.release.set()
                await asyncio.wait_for(repair, 5)
                with pytest.raises(HTTPException) as conflict:
                    await asyncio.wait_for(deletion, 5)
                assert conflict.value.status_code == 409
                post, attached = await assert_preserved(store, case)
                assert post.media_url and post.thumbnail and len(attached) == 2
                assert not case.deleted
        finally:
            repair_db.release.set()
            release_storage.set()
            await finish_tasks(repair, deletion)


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["post_url", "shared_object"])
async def test_deletion_waits_then_preserves_other_committed_live_reference(store, media_case, reference):
    case = media_case
    primary = await attach(store, case)
    primary_id = uuid.UUID(str(primary["id"]))
    deletion = None
    async with store.sessions() as reference_db, store.sessions() as delete_db:
        try:
            await PostRepository(reference_db).get_by_id(case.post_id, for_update=True)
            original = await MediaRepository(reference_db).get_by_id(primary_id)
            assert original is not None
            key = original.storage_key
            delete_pid = await delete_db.scalar(text("SELECT pg_backend_pid()"))
            deletion = asyncio.create_task(delete_media(primary_id, case.user, delete_db))
            await wait_for_blockers(store, [delete_pid], 1)
            other = Post(user_id=case.user.id, content_type=ContentType.VIDEO, status=ContentStatus.DRAFT)
            reference_db.add(other)
            await reference_db.flush()
            if reference == "post_url":
                other.media_url = original.url
            else:
                shared_id = uuid.uuid4()
                shared = Media(
                    id=shared_id, post_id=other.id, user_id=case.user.id, media_type="video/mp4",
                    url=f"/media/{shared_id}/content", storage_key=key, storage_provider="s3",
                )
                other.media_url = shared.url
                reference_db.add(shared)
            await reference_db.commit()
            if reference == "post_url":
                with pytest.raises(HTTPException) as conflict:
                    await asyncio.wait_for(deletion, 5)
                assert conflict.value.status_code == 409
            else:
                await asyncio.wait_for(deletion, 5)
        finally:
            await reference_db.rollback()
            await finish_tasks(deletion)
    assert not case.deleted and key in case.objects
    await assert_preserved(store, case)
    async with store.sessions() as db:
        persisted = await PostRepository(db).get_by_id(other.id)
        assert persisted is not None and persisted.media_url
        active = await db.scalar(select(Media.id).where(
            Media.url == persisted.media_url, Media.deleted_at.is_(None),
        ))
        assert active is not None


@pytest.mark.asyncio
async def test_attachment_during_post_commit_cleanup_uses_a_different_object(store, media_case, monkeypatch):
    case = media_case
    primary = await attach(store, case)
    primary_id = uuid.UUID(str(primary["id"]))
    async with store.sessions() as db:
        old = await MediaRepository(db).get_by_id(primary_id)
        assert old is not None
        old_key = old.storage_key
    cleaning, release = asyncio.Event(), asyncio.Event()
    original_cleanup = media_storage.delete

    async def cleanup(key):
        cleaning.set()
        await release.wait()
        await original_cleanup(key)

    monkeypatch.setattr("features.media.router.media_storage.delete", cleanup)
    deletion = None
    async with store.sessions() as delete_db:
        try:
            deletion = asyncio.create_task(delete_media(primary_id, case.user, delete_db))
            await asyncio.wait_for(cleaning.wait(), 5)
            # Cleanup is paused; a real competing attachment must not wait on a post lock.
            replacement = await asyncio.wait_for(attach(store, case), 5)
            assert replacement["id"] != primary["id"]
            post, attached = await assert_preserved(store, case)
            assert post.media_url == replacement["url"] and len(attached) == 1
            assert attached[0].storage_key != old_key
            release.set()
            await asyncio.wait_for(deletion, 5)
            assert case.deleted == [old_key]
            await assert_preserved(store, case)
        finally:
            release.set()
            await finish_tasks(deletion)


class LostCommitAcknowledgementSession(AsyncSession):
    persist: bool

    async def commit(self):
        if self.persist:
            await super().commit()
        else:
            await self.flush()
        raise ConnectionError("Injected missing commit acknowledgement")


@pytest.mark.asyncio
@pytest.mark.parametrize("persist", [True, False])
async def test_uncertain_commit_preserves_object_with_real_transaction(store, media_case, persist):
    """Inject acknowledgement loss around a real commit/rollback, not a network fault."""
    case = media_case
    upload_id = uuid.uuid4()
    db = LostCommitAcknowledgementSession(bind=store.engine, expire_on_commit=False, autoflush=False)
    db.persist = persist
    async with db:
        with pytest.raises(HTTPException) as error:
            await attach(store, case, db, upload_id=upload_id)
        assert error.value.status_code == 503
    assert len(case.objects) == 1 and not case.deleted
    post, attached = await assert_preserved(store, case)
    assert bool(post.media_url) is persist
    assert len(attached) == int(persist)
    async with store.sessions() as observer:
        receipt = await get_upload_receipt(
            case.post_id, upload_id, "primary", current_user=case.user, db=observer,
        )
        if persist:
            assert receipt["id"] == str(upload_id)
        else:
            assert receipt == {"attachment": None}
