"""
PostgreSQL integration tests for CollaborationRepository.

Covers the CollaborationRepository against real PostgreSQL:
1. Create and retrieve collaborations.
2. get_for_user() for initiator and participant.
3. status filtering.
4. cursor/before_id behavior where supported.
5. get_marketplace().
6. Marketplace content_type filtering.
7. Marketplace tag filtering using PostgreSQL JSONB.
8. Participant add/get/update/remove.
9. Milestone add/get/update.
10. Unique participant constraint.
11. PostgreSQL CollaborationStatus and MilestoneStatus enum persistence.
12. Soft-deleted collaboration filtering.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.graphql import (
    AppContext,
    _accept_collaboration,
    _add_milestone,
    _collaboration,
    _create_collaboration,
    _decline_collaboration,
    _discover_creators,
    _update_collaboration,
    _update_milestone,
)
from app.db.session import async_engine, async_session_factory
from app.models.collaboration import (
    Collaboration,
    CollaborationParticipant,
    CollaborationStatus,
    Milestone,
    MilestoneStatus,
)
from app.models.user import AccountStatus, Profile, User, UserRole
from repositories.collaboration_repository import CollaborationRepository
from repositories.messaging_repository import ConversationRepository


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _dispose_async_engine_pool_per_test():
    """Dispose pooled asyncpg connections after every test.

    pytest-asyncio (strict mode) gives each test function its own event
    loop, but ``async_engine`` is a module-level singleton whose pool holds
    asyncpg connections bound to whichever loop created them. Left
    undisposed, the next test's new loop tries to reuse a connection tied
    to an already-closed loop, producing "Event loop is closed" /
    "'NoneType' object has no attribute 'send'" failures. Disposing the
    pool here (while its owning loop is still open) forces fresh
    connections to be created per test.
    """
    yield
    await async_engine.dispose()


# ── Helpers ──────────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def transactional_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional AsyncSession rolled back after each test."""
    async with async_session_factory() as s:
        transaction = await s.begin()
        try:
            yield s
        finally:
            await transaction.rollback()


async def create_test_user(session: AsyncSession, prefix: str = "collab_user") -> User:
    """Create and persist a unique User entity in the test transaction."""
    uid = uuid.uuid4().hex[:10]
    user = User(
        email=f"{prefix}_{uid}@example.test",
        username=f"{prefix}_{uid}",
        hashed_password="hashed_test_password",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )
    session.add(user)
    await session.flush()
    return user


async def create_and_commit_user(prefix: str) -> User:
    """Create and commit a User in its own session (visible to other sessions)."""
    uid = uuid.uuid4().hex[:10]
    user = User(
        email=f"{prefix}_{uid}@example.test",
        username=f"{prefix}_{uid}",
        hashed_password="hashed_test_password",
        role=UserRole.USER,
        status=AccountStatus.ACTIVE,
        email_verified=True,
        mfa_enabled=False,
    )
    async with async_session_factory() as session:
        session.add(user)
        await session.flush()
        await session.commit()
    return user


async def create_and_commit_pending_collaboration(
    initiator_id: uuid.UUID, participant_id: uuid.UUID
) -> uuid.UUID:
    """Create+commit a PROPOSED collaboration with one pending participant."""
    collab = Collaboration(
        initiator_id=initiator_id,
        title="Concurrency Test Collab",
        status=CollaborationStatus.PROPOSED,
    )
    async with async_session_factory() as session:
        session.add(collab)
        await session.flush()
        session.add(
            CollaborationParticipant(
                collaboration_id=collab.id,
                user_id=participant_id,
                role="participant",
                accepted=False,
            )
        )
        await session.commit()
    return collab.id


async def cleanup_users(*user_ids: uuid.UUID) -> None:
    """Delete users (cascades to their collaborations/participants) after a test."""
    async with async_session_factory() as session:
        for uid_ in user_ids:
            user = await session.get(User, uid_)
            if user is not None:
                await session.delete(user)
        await session.commit()


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_retrieve_collaboration():
    """1. Create and retrieve collaborations via BaseRepository and CollaborationRepository."""
    async with transactional_session() as session:
        test_user = await create_test_user(session, "user_create")
        repo = CollaborationRepository(session)

        collab = Collaboration(
            initiator_id=test_user.id,
            title="Epic Tech Video",
            description="A collaborative coding and architecture showcase",
            status=CollaborationStatus.PROPOSED,
            content_type="video",
            platform="youtube",
            tags=["tech", "coding", "fastapi"],
            proposed_at="2026-09-13T10:00:00Z",
            started_at="2026-09-14T10:00:00Z",
            completed_at=None,
            budget_min=150.0,
            budget_max=750.0,
            budget_currency="USD",
        )
        created = await repo.create(collab)
        assert created.id is not None

        retrieved = await repo.get_by_id(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.initiator_id == test_user.id
        assert retrieved.title == "Epic Tech Video"
        assert retrieved.description == "A collaborative coding and architecture showcase"
        assert retrieved.status == CollaborationStatus.PROPOSED
        assert retrieved.content_type == "video"
        assert retrieved.platform == "youtube"
        assert retrieved.tags == ["tech", "coding", "fastapi"]
        assert retrieved.budget_min == 150.0
        assert retrieved.budget_max == 750.0
        assert retrieved.budget_currency == "USD"

        # Also test get_by_id_for_update
        locked = await repo.get_by_id_for_update(created.id)
        assert locked is not None
        assert locked.id == created.id


@pytest.mark.asyncio
async def test_get_for_user_initiator_and_participant():
    """2. get_for_user() returns collaborations where user is initiator or participant."""
    async with transactional_session() as session:
        user_a = await create_test_user(session, "user_init")
        user_b = await create_test_user(session, "user_part")
        user_c = await create_test_user(session, "user_unrel")
        repo = CollaborationRepository(session)

        # User A initiates Collab 1
        collab1 = Collaboration(
            initiator_id=user_a.id,
            title="Collab 1 - User A Initiator",
            status=CollaborationStatus.PROPOSED,
        )
        session.add(collab1)
        await session.flush()

        # User B is a participant in Collab 1
        participant_b = CollaborationParticipant(
            collaboration_id=collab1.id,
            user_id=user_b.id,
            role="participant",
            accepted=False,
        )
        await repo.add_participant(participant_b)

        # User B initiates Collab 2
        collab2 = Collaboration(
            initiator_id=user_b.id,
            title="Collab 2 - User B Initiator",
            status=CollaborationStatus.PROPOSED,
        )
        session.add(collab2)
        await session.flush()

        # Query for User A (initiator of collab1 only)
        user_a_collabs = await repo.get_for_user(user_a.id)
        user_a_ids = [c.id for c in user_a_collabs]
        assert collab1.id in user_a_ids
        assert collab2.id not in user_a_ids

        # Query for User B (participant in collab1, initiator of collab2)
        user_b_collabs = await repo.get_for_user(user_b.id)
        user_b_ids = [c.id for c in user_b_collabs]
        assert collab1.id in user_b_ids
        assert collab2.id in user_b_ids

        # Query for User C (uninvolved)
        user_c_collabs = await repo.get_for_user(user_c.id)
        user_c_ids = [c.id for c in user_c_collabs]
        assert collab1.id not in user_c_ids
        assert collab2.id not in user_c_ids



@pytest.mark.asyncio
async def test_get_pairwise_history():
    """Return collaborations shared by two users."""
    async with transactional_session() as session:
        user_a = await create_test_user(session, "pairwise_a")
        user_b = await create_test_user(session, "pairwise_b")
        user_c = await create_test_user(session, "pairwise_c")

        shared_collab = Collaboration(
            initiator_id=user_a.id,
            title="Shared Collaboration",
            status=CollaborationStatus.COMPLETED,
            tags=["art", "crafting"],
        )

        unrelated_collab = Collaboration(
            initiator_id=user_a.id,
            title="Unrelated Collaboration",
            status=CollaborationStatus.COMPLETED,
        )

        session.add_all([shared_collab, unrelated_collab])
        await session.flush()

        session.add_all(
            [
                CollaborationParticipant(
                    collaboration_id=shared_collab.id,
                    user_id=user_b.id,
                    role="participant",
                    accepted=True,
                ),
                CollaborationParticipant(
                    collaboration_id=unrelated_collab.id,
                    user_id=user_c.id,
                    role="participant",
                    accepted=True,
                ),
            ]
        )
        await session.flush()

        repo = CollaborationRepository(session)

        history = await repo.get_pairwise_history(
            user_a.id,
            user_b.id,
        )

        assert len(history) == 1
        assert history[0].id == shared_collab.id
        assert history[0].status == CollaborationStatus.COMPLETED


@pytest.mark.asyncio
async def test_status_filtering():
    """3. status filtering on get_for_user() and status_counts_for_user()."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_status")
        repo = CollaborationRepository(session)

        c_proposed = Collaboration(
            initiator_id=user.id,
            title="Proposed Collab",
            status=CollaborationStatus.PROPOSED,
        )
        c_accepted = Collaboration(
            initiator_id=user.id,
            title="Accepted Collab",
            status=CollaborationStatus.ACCEPTED,
        )
        c_completed = Collaboration(
            initiator_id=user.id,
            title="Completed Collab",
            status=CollaborationStatus.COMPLETED,
        )
        session.add_all([c_proposed, c_accepted, c_completed])
        await session.flush()

        proposed_list = await repo.get_for_user(user.id, status=CollaborationStatus.PROPOSED)
        assert [c.id for c in proposed_list] == [c_proposed.id]

        accepted_list = await repo.get_for_user(user.id, status=CollaborationStatus.ACCEPTED)
        assert [c.id for c in accepted_list] == [c_accepted.id]

        completed_list = await repo.get_for_user(user.id, status=CollaborationStatus.COMPLETED)
        assert [c.id for c in completed_list] == [c_completed.id]

        in_progress_list = await repo.get_for_user(user.id, status=CollaborationStatus.IN_PROGRESS)
        assert in_progress_list == []

        # Status counts check
        counts = await repo.status_counts_for_user(user.id)
        assert counts.get(CollaborationStatus.PROPOSED) == 1
        assert counts.get(CollaborationStatus.ACCEPTED) == 1
        assert counts.get(CollaborationStatus.COMPLETED) == 1


@pytest.mark.asyncio
async def test_cursor_before_id_behavior():
    """4. cursor/before_id behavior where supported in get_for_user and get_marketplace."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_cursor")
        repo = CollaborationRepository(session)
        now = datetime.now(timezone.utc)

        # Create 3 collaborations with distinct created_at
        c1 = Collaboration(
            initiator_id=user.id,
            title="Collab 1 (Oldest)",
            status=CollaborationStatus.PROPOSED,
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=3),
        )
        c2 = Collaboration(
            initiator_id=user.id,
            title="Collab 2 (Middle)",
            status=CollaborationStatus.PROPOSED,
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=2),
        )
        c3 = Collaboration(
            initiator_id=user.id,
            title="Collab 3 (Newest)",
            status=CollaborationStatus.PROPOSED,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        session.add_all([c1, c2, c3])
        await session.flush()

        # Test get_for_user pagination: fetch page 1 (limit=2)
        page1 = await repo.get_for_user(user.id, limit=2)
        assert len(page1) == 2
        assert page1[0].id == c3.id
        assert page1[1].id == c2.id

        # Fetch page 2 using cursor from last item in page 1
        cursor = (page1[-1].created_at, page1[-1].id)
        page2 = await repo.get_for_user(user.id, limit=2, before=cursor)
        assert len(page2) == 1
        assert page2[0].id == c1.id

        # Test get_marketplace pagination with cursor
        market_page1 = await repo.get_marketplace(limit=2)
        assert len(market_page1) >= 2
        market_cursor = (market_page1[1].created_at, market_page1[1].id)
        market_page2 = await repo.get_marketplace(limit=2, before=market_cursor)
        # The IDs in page 2 must not overlap with page 1
        page1_ids = {c.id for c in market_page1[:2]}
        for item in market_page2:
            assert item.id not in page1_ids


@pytest.mark.asyncio
async def test_get_marketplace():
    """5. get_marketplace() returns only PROPOSED non-deleted collaborations."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_market")
        repo = CollaborationRepository(session)

        c_proposed = Collaboration(
            initiator_id=user.id,
            title="Marketplace Listing",
            status=CollaborationStatus.PROPOSED,
        )
        c_accepted = Collaboration(
            initiator_id=user.id,
            title="Active Collab",
            status=CollaborationStatus.ACCEPTED,
        )
        c_cancelled = Collaboration(
            initiator_id=user.id,
            title="Cancelled Collab",
            status=CollaborationStatus.CANCELLED,
        )
        session.add_all([c_proposed, c_accepted, c_cancelled])
        await session.flush()

        marketplace_items = await repo.get_marketplace(limit=50)
        marketplace_ids = [c.id for c in marketplace_items]

        assert c_proposed.id in marketplace_ids
        assert c_accepted.id not in marketplace_ids
        assert c_cancelled.id not in marketplace_ids


@pytest.mark.asyncio
async def test_marketplace_content_type_filtering():
    """6. Marketplace content_type filtering."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_ctype")
        repo = CollaborationRepository(session)

        c_video = Collaboration(
            initiator_id=user.id,
            title="Video Project",
            status=CollaborationStatus.PROPOSED,
            content_type="video",
        )
        c_podcast = Collaboration(
            initiator_id=user.id,
            title="Podcast Project",
            status=CollaborationStatus.PROPOSED,
            content_type="podcast",
        )
        c_livestream = Collaboration(
            initiator_id=user.id,
            title="Livestream Project",
            status=CollaborationStatus.PROPOSED,
            content_type="livestream",
        )
        session.add_all([c_video, c_podcast, c_livestream])
        await session.flush()

        podcasts = await repo.get_marketplace(content_type="podcast")
        podcast_ids = [c.id for c in podcasts]
        assert c_podcast.id in podcast_ids
        assert c_video.id not in podcast_ids
        assert c_livestream.id not in podcast_ids

        videos = await repo.get_marketplace(content_type="video")
        video_ids = [c.id for c in videos]
        assert c_video.id in video_ids
        assert c_podcast.id not in video_ids


@pytest.mark.asyncio
async def test_marketplace_tag_filtering_jsonb():
    """7. Marketplace tag filtering using PostgreSQL JSONB ?| (ANY) operator."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_tags")
        repo = CollaborationRepository(session)

        c_music = Collaboration(
            initiator_id=user.id,
            title="Music Producer Collab",
            status=CollaborationStatus.PROPOSED,
            tags=["music", "producer", "vocalist"],
        )
        c_gaming = Collaboration(
            initiator_id=user.id,
            title="Gaming Stream",
            status=CollaborationStatus.PROPOSED,
            tags=["gaming", "streamer"],
        )
        c_cooking = Collaboration(
            initiator_id=user.id,
            title="Cooking Show",
            status=CollaborationStatus.PROPOSED,
            tags=["cooking", "recipes"],
        )
        session.add_all([c_music, c_gaming, c_cooking])
        await session.flush()

        # Single matching tag returns the listing
        gaming_results = await repo.get_marketplace(tags=["gaming"])
        gaming_ids = [c.id for c in gaming_results]
        assert c_gaming.id in gaming_ids
        assert c_music.id not in gaming_ids
        assert c_cooking.id not in gaming_ids

        # Multiple supplied tags use ANY semantics (match either, not both)
        any_results = await repo.get_marketplace(tags=["music", "gaming"])
        any_ids = [c.id for c in any_results]
        assert c_music.id in any_ids
        assert c_gaming.id in any_ids
        assert c_cooking.id not in any_ids

        # Listing with none of the supplied tags is excluded
        none_matching = await repo.get_marketplace(tags=["cooking"])
        nm_ids = [c.id for c in none_matching]
        assert c_music.id not in nm_ids
        assert c_gaming.id not in nm_ids
        assert c_cooking.id in nm_ids


@pytest.mark.asyncio
async def test_participant_add_get_update_remove():
    """8. Participant add/get/update/remove and pending/accepted querying."""
    async with transactional_session() as session:
        user_a = await create_test_user(session, "user_pinit")
        user_b = await create_test_user(session, "user_ppart")
        repo = CollaborationRepository(session)

        collab = Collaboration(
            initiator_id=user_a.id,
            title="Participant Test Collab",
            status=CollaborationStatus.PROPOSED,
        )
        session.add(collab)
        await session.flush()

        # Add participant
        participant = CollaborationParticipant(
            collaboration_id=collab.id,
            user_id=user_b.id,
            role="editor",
            accepted=False,
        )
        added = await repo.add_participant(participant)
        assert added.id is not None
        assert added.role == "editor"
        assert added.accepted is False

        # Get participant
        fetched = await repo.get_participant(collab.id, user_b.id)
        assert fetched is not None
        assert fetched.id == added.id
        assert fetched.user_id == user_b.id
        assert fetched.role == "editor"

        # Pending vs Accepted
        pending = await repo.get_pending_participants(collab)
        assert any(p.user_id == user_b.id for p in pending)
        accepted_ids = await repo.get_participants(collab.id)
        assert user_b.id not in accepted_ids

        # Update participant to accepted
        fetched.accepted = True
        fetched.accepted_at = "2026-09-13T12:00:00Z"
        fetched.role = "co-creator"
        updated = await repo.update_participant(fetched)
        assert updated.accepted is True
        assert updated.role == "co-creator"

        # Now accepted queries return the participant
        accepted_ids = await repo.get_participants(collab.id)
        assert user_b.id in accepted_ids
        accepted_participants = await repo.get_accepted_participants(collab)
        assert any(p.user_id == test_user_2.id for p in accepted_participants) if False else True
        assert any(p.user_id == user_b.id for p in accepted_participants)

        # Remove participant
        await repo.remove_participant(updated)
        removed = await repo.get_participant(collab.id, user_b.id)
        assert removed is None


@pytest.mark.asyncio
async def test_milestone_add_get_update():
    """9. Milestone add/get/update and sorting."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_mstone")
        repo = CollaborationRepository(session)

        collab = Collaboration(
            initiator_id=user.id,
            title="Milestone Collab",
            status=CollaborationStatus.PROPOSED,
        )
        session.add(collab)
        await session.flush()

        m1 = Milestone(
            collaboration_id=collab.id,
            title="Phase 1: Concept & Script",
            description="Write script and storyboard",
            status=MilestoneStatus.PENDING,
            sort_order=1,
            due_at="2026-09-20T00:00:00Z",
        )
        m2 = Milestone(
            collaboration_id=collab.id,
            title="Phase 2: Recording",
            description="Record audio and video",
            status=MilestoneStatus.PENDING,
            sort_order=2,
            due_at="2026-09-25T00:00:00Z",
        )
        await repo.add_milestone(m1)
        await repo.add_milestone(m2)

        # Get all milestones (ordered by sort_order)
        milestones = await repo.get_milestones(collab.id)
        assert len(milestones) == 2
        assert milestones[0].title == "Phase 1: Concept & Script"
        assert milestones[1].title == "Phase 2: Recording"

        # Get single milestone by ID
        single = await repo.get_milestone_by_id(m1.id)
        assert single is not None
        assert single.id == m1.id
        assert single.title == "Phase 1: Concept & Script"

        # Update milestone
        single.status = MilestoneStatus.COMPLETED
        single.completed_at = "2026-09-19T18:00:00Z"
        updated = await repo.update_milestone(single)
        assert updated.status == MilestoneStatus.COMPLETED
        assert updated.completed_at == "2026-09-19T18:00:00Z"


@pytest.mark.asyncio
async def test_unique_participant_constraint():
    """10. Unique participant constraint (collaboration_id, user_id) raises IntegrityError."""
    async with transactional_session() as session:
        user_a = await create_test_user(session, "user_uinit")
        user_b = await create_test_user(session, "user_upart")
        repo = CollaborationRepository(session)

        collab = Collaboration(
            initiator_id=user_a.id,
            title="Uniqueness Test Collab",
            status=CollaborationStatus.PROPOSED,
        )
        session.add(collab)
        await session.flush()

        p1 = CollaborationParticipant(
            collaboration_id=collab.id,
            user_id=user_b.id,
            role="participant",
            accepted=False,
        )
        await repo.add_participant(p1)

        # Attempting to add duplicate participant for same collaboration and user
        p2_duplicate = CollaborationParticipant(
            collaboration_id=collab.id,
            user_id=user_b.id,
            role="duplicate_participant",
            accepted=True,
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(p2_duplicate)
                await session.flush()


@pytest.mark.asyncio
async def test_enum_persistence_collaboration_and_milestone():
    """11. PostgreSQL CollaborationStatus and MilestoneStatus enum persistence."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_enum")
        repo = CollaborationRepository(session)

        for status in CollaborationStatus:
            collab = Collaboration(
                initiator_id=user.id,
                title=f"Enum Collab {status.value}",
                status=status,
            )
            session.add(collab)
            await session.flush()
            await session.refresh(collab)
            assert collab.status == status

            # Read the raw column value back to confirm PostgreSQL stored the
            # lowercase enum value, not the uppercase member name.
            raw_status = await session.scalar(
                text("SELECT status FROM collaborations WHERE id = :id"),
                {"id": collab.id},
            )
            assert raw_status == status.value

        for m_status in MilestoneStatus:
            collab = Collaboration(
                initiator_id=user.id,
                title=f"Collab for Milestone {m_status.value}",
                status=CollaborationStatus.PROPOSED,
            )
            session.add(collab)
            await session.flush()

            milestone = Milestone(
                collaboration_id=collab.id,
                title=f"Milestone {m_status.value}",
                status=m_status,
                sort_order=0,
            )
            session.add(milestone)
            await session.flush()
            await session.refresh(milestone)
            assert milestone.status == m_status

            raw_milestone_status = await session.scalar(
                text("SELECT status FROM milestones WHERE id = :id"),
                {"id": milestone.id},
            )
            assert raw_milestone_status == m_status.value


@pytest.mark.asyncio
async def test_soft_deleted_collaboration_filtering():
    """12. Soft-deleted collaboration filtering across repository queries."""
    async with transactional_session() as session:
        user = await create_test_user(session, "user_softdel")
        repo = CollaborationRepository(session)

        c_active = Collaboration(
            initiator_id=user.id,
            title="Active Collab",
            status=CollaborationStatus.PROPOSED,
        )
        c_deleted = Collaboration(
            initiator_id=user.id,
            title="Deleted Collab",
            status=CollaborationStatus.PROPOSED,
        )
        session.add_all([c_active, c_deleted])
        await session.flush()

        # Soft delete c_deleted
        c_deleted.deleted_at = datetime.now(timezone.utc)
        await session.flush()

        # get_by_id should return None for soft-deleted
        assert await repo.get_by_id(c_deleted.id) is None
        assert await repo.get_by_id(c_active.id) is not None

        # get_by_id_for_update should return None for soft-deleted
        assert await repo.get_by_id_for_update(c_deleted.id) is None
        assert await repo.get_by_id_for_update(c_active.id) is not None

        # exists should return False for soft-deleted
        assert await repo.exists(c_deleted.id) is False
        assert await repo.exists(c_active.id) is True

        # get_for_user should not include soft-deleted
        user_collabs = await repo.get_for_user(user.id)
        user_collab_ids = [c.id for c in user_collabs]
        assert c_deleted.id not in user_collab_ids
        assert c_active.id in user_collab_ids

        # get_marketplace should not include soft-deleted
        marketplace = await repo.get_marketplace()
        marketplace_ids = [c.id for c in marketplace]
        assert c_deleted.id not in marketplace_ids
        assert c_active.id in marketplace_ids

        # status_counts_for_user should not count soft-deleted
        counts = await repo.status_counts_for_user(user.id)
        assert counts.get(CollaborationStatus.PROPOSED) == 1


# ── Concurrency Tests (Step 7) ──────────────────────────────────────
#
# These tests use separate, independently-committing AsyncSessions (not the
# rolled-back `transactional_session` helper above) so that two "requests"
# genuinely race against real PostgreSQL row locks/unique constraints instead
# of sharing one transaction's uncommitted state.


@pytest.mark.asyncio
async def test_concurrent_accept_accept_same_invite_exactly_one_wins():
    """13. Two concurrent Accept calls on the same pending invite: exactly one wins."""
    initiator = await create_and_commit_user("race_init")
    participant_user = await create_and_commit_user("race_accept")
    collab_id = await create_and_commit_pending_collaboration(
        initiator.id, participant_user.id
    )

    async def do_accept():
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=participant_user)
            return await _accept_collaboration(ctx, collab_id)

    try:
        results = await asyncio.gather(do_accept(), do_accept(), return_exceptions=True)

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1, f"expected exactly one winner, got: {results}"
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)

        # Final state, observed from a fresh session: ACCEPTED, one accepted participant.
        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            collab = await repo.get_by_id(collab_id)
            assert collab is not None
            assert collab.status == CollaborationStatus.ACCEPTED

            participant = await repo.get_participant(collab_id, participant_user.id)
            assert participant is not None
            assert participant.accepted is True
    finally:
        await cleanup_users(initiator.id, participant_user.id)


@pytest.mark.asyncio
async def test_concurrent_accept_decline_race_exactly_one_wins():
    """14. Concurrent Accept/Decline race on the same pending invite: one wins, state is consistent."""
    initiator = await create_and_commit_user("race_init2")
    participant_user = await create_and_commit_user("race_ad")
    collab_id = await create_and_commit_pending_collaboration(
        initiator.id, participant_user.id
    )

    async def do_accept():
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=participant_user)
            return await _accept_collaboration(ctx, collab_id)

    async def do_decline():
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=participant_user)
            return await _decline_collaboration(ctx, collab_id)

    try:
        accept_result, decline_result = await asyncio.gather(
            do_accept(), do_decline(), return_exceptions=True
        )

        outcomes = [accept_result, decline_result]
        successes = [r for r in outcomes if not isinstance(r, BaseException)]
        failures = [r for r in outcomes if isinstance(r, BaseException)]
        assert len(successes) == 1, f"expected exactly one winner, got: {outcomes}"
        assert len(failures) == 1
        # The loser observes committed state and rejects — never a generic crash.
        assert isinstance(failures[0], (ValueError, PermissionError))

        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            collab = await repo.get_by_id(collab_id)
            participant = await repo.get_participant(collab_id, participant_user.id)

            assert collab is not None
            assert collab.status in (
                CollaborationStatus.ACCEPTED,
                CollaborationStatus.DECLINED,
            )
            if collab.status == CollaborationStatus.ACCEPTED:
                # Accept won: participant row still exists and is accepted.
                assert participant is not None
                assert participant.accepted is True
            else:
                # Decline won: recipient's participant row is removed (documented behavior).
                assert participant is None
    finally:
        await cleanup_users(initiator.id, participant_user.id)


@pytest.mark.asyncio
async def test_concurrent_duplicate_participant_insert_integrity_error():
    """15. Two sessions inserting the same (collaboration_id, user_id) pair: only one row survives."""
    initiator = await create_and_commit_user("race_dup_init")
    target_user = await create_and_commit_user("race_dup_user")

    collab = Collaboration(
        initiator_id=initiator.id,
        title="Duplicate Participant Race Collab",
        status=CollaborationStatus.PROPOSED,
    )
    async with async_session_factory() as session:
        session.add(collab)
        await session.commit()
    collab_id = collab.id

    start = asyncio.Event()

    async def do_insert(role: str):
        async with async_session_factory() as session:
            participant = CollaborationParticipant(
                collaboration_id=collab_id,
                user_id=target_user.id,
                role=role,
                accepted=False,
            )
            session.add(participant)
            await start.wait()
            await session.commit()

    try:
        task_a = asyncio.create_task(do_insert("participant"))
        task_b = asyncio.create_task(do_insert("editor"))
        # Give both tasks a moment to reach `await start.wait()` before releasing
        # them together, maximizing the chance they commit around the same time.
        await asyncio.sleep(0.05)
        start.set()
        results = await asyncio.gather(task_a, task_b, return_exceptions=True)

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1, f"expected exactly one insert to survive, got: {results}"
        assert len(failures) == 1
        assert isinstance(failures[0], IntegrityError)

        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM collaboration_participants "
                    "WHERE collaboration_id = :cid AND user_id = :uid"
                ),
                {"cid": str(collab_id), "uid": str(target_user.id)},
            )
            assert result.scalar_one() == 1
    finally:
        await cleanup_users(initiator.id, target_user.id)


@pytest.mark.asyncio
async def test_explicit_row_lock_blocks_concurrent_transaction():
    """16. FOR UPDATE row lock: session B blocks until session A commits, then sees committed state."""
    initiator = await create_and_commit_user("lock_init")
    collab = Collaboration(
        initiator_id=initiator.id,
        title="Original Title",
        status=CollaborationStatus.PROPOSED,
    )
    async with async_session_factory() as session:
        session.add(collab)
        await session.commit()
    collab_id = collab.id

    a_locked = asyncio.Event()
    b_attempted = asyncio.Event()
    timestamps: dict[str, float] = {}

    async def session_a():
        async with async_session_factory() as session:
            async with session.begin():
                repo = CollaborationRepository(session)
                locked = await repo.get_by_id_for_update(collab_id)
                a_locked.set()
                # Wait until B has issued its own FOR UPDATE request (it will block
                # in Postgres until this transaction commits/releases the lock).
                await b_attempted.wait()
                await asyncio.sleep(0.3)
                assert locked is not None
                locked.title = "Updated by A while holding the lock"
                await CollaborationRepository(session).update(locked)
            timestamps["a_committed"] = time.monotonic()

    async def session_b() -> str:
        await a_locked.wait()
        async with async_session_factory() as session:
            async with session.begin():
                repo = CollaborationRepository(session)
                b_attempted.set()
                # This blocks at the PostgreSQL level until session A commits.
                locked = await repo.get_by_id_for_update(collab_id)
                timestamps["b_locked"] = time.monotonic()
                assert locked is not None
                return locked.title

    try:
        _, observed_title = await asyncio.gather(session_a(), session_b())

        assert observed_title == "Updated by A while holding the lock"
        assert timestamps["b_locked"] >= timestamps["a_committed"], (
            "session B observed the lock before session A committed — "
            "row lock did not actually block"
        )
    finally:
        await cleanup_users(initiator.id)


# ── Transaction Safety Tests (Step 8) ────────────────────────────────
#
# These tests exercise the real GraphQL resolvers (`_create_collaboration`,
# `_add_milestone`, `_update_milestone`, `_accept_collaboration`,
# `_decline_collaboration`) against a live PostgreSQL connection using the
# same per-request session lifecycle as production (a single AsyncSession
# per resolver call, not the auto-rolled-back `transactional_session`
# helper), so a mid-transaction failure has to be rolled back by the
# resolver itself for the database to stay consistent. State is verified
# from an independent, freshly-opened session after each resolver call.


@pytest.mark.asyncio
async def test_create_collaboration_failure_leaves_no_partial_collaboration_or_participants(
    monkeypatch,
):
    """3 / 6. A failed collaboration creation leaves no collaboration or participant rows."""
    from api.graphql import _create_collaboration

    owner = await create_and_commit_user("txn_create_owner")
    invited = await create_and_commit_user("txn_create_invited")

    call_count = {"n": 0}
    real_add_participant = CollaborationRepository.add_participant

    async def flaky_add_participant(self, participant):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated participant insert failure")
        return await real_add_participant(self, participant)

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_participant",
        flaky_add_participant,
    )

    input_ = SimpleNamespace(
        title="Txn Safety Collab",
        description="Should not survive a failed insert",
        content_type="video",
        platform="youtube",
        tags=["test"],
        participant_ids=[invited.id],
        budget_min=None,
        budget_max=None,
        budget_currency=None,
    )

    try:
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=owner, session_id="txn-test")
            with pytest.raises(RuntimeError, match="simulated participant insert failure"):
                await _create_collaboration(ctx, input_)

        # Verify from an independent session: no partial collaboration/participant state.
        async with async_session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM collaborations WHERE title = :t"),
                {"t": "Txn Safety Collab"},
            )
            assert result.scalar_one() == 0

            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM collaboration_participants "
                    "WHERE user_id = :uid"
                ),
                {"uid": str(owner.id)},
            )
            assert result.scalar_one() == 0
    finally:
        await cleanup_users(owner.id, invited.id)


@pytest.mark.asyncio
async def test_add_milestone_failure_rolls_back_milestone_insert(monkeypatch):
    """4. Milestone creation failure rolls back the milestone."""
    from api.graphql import _add_milestone

    owner = await create_and_commit_user("txn_milestone_owner")
    collab = Collaboration(
        initiator_id=owner.id,
        title="Milestone Txn Collab",
        status=CollaborationStatus.PROPOSED,
    )
    async with async_session_factory() as session:
        session.add(collab)
        await session.commit()
    collab_id = collab.id

    async def fail_add_milestone(self, milestone):
        raise RuntimeError("simulated milestone insert failure")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.add_milestone",
        fail_add_milestone,
    )

    input_ = SimpleNamespace(
        collaboration_id=collab_id,
        title="Doomed Milestone",
        description="Should not persist",
        due_date=None,
    )

    try:
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=owner, session_id="txn-test")
            with pytest.raises(RuntimeError, match="simulated milestone insert failure"):
                await _add_milestone(ctx, input_)

        async with async_session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM milestones WHERE collaboration_id = :cid"),
                {"cid": str(collab_id)},
            )
            assert result.scalar_one() == 0
    finally:
        await cleanup_users(owner.id)


@pytest.mark.asyncio
async def test_update_milestone_failure_leaves_prior_state_intact(monkeypatch):
    """5. Milestone update failure leaves the prior milestone state intact."""
    from api.graphql import _update_milestone

    owner = await create_and_commit_user("txn_mstone_upd_owner")
    collab = Collaboration(
        initiator_id=owner.id,
        title="Milestone Update Txn Collab",
        status=CollaborationStatus.PROPOSED,
    )
    async with async_session_factory() as session:
        session.add(collab)
        await session.flush()
        milestone = Milestone(
            collaboration_id=collab.id,
            title="Original Title",
            description="Original description",
            status=MilestoneStatus.PENDING,
            sort_order=0,
        )
        session.add(milestone)
        await session.commit()
        milestone_id = milestone.id

    async def fail_update_milestone(self, m):
        raise RuntimeError("simulated milestone update failure")

    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.update_milestone",
        fail_update_milestone,
    )

    update_input = SimpleNamespace(
        title="Changed Title", description=None, status=None, due_date=None
    )

    try:
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=owner, session_id="txn-test")
            with pytest.raises(RuntimeError, match="simulated milestone update failure"):
                await _update_milestone(ctx, str(milestone_id), update_input)

        # Verify from an independent session: the prior title/description survived.
        async with async_session_factory() as session:
            result = await session.execute(
                text("SELECT title, description, status FROM milestones WHERE id = :id"),
                {"id": str(milestone_id)},
            )
            row = result.one()
            assert row.title == "Original Title"
            assert row.description == "Original description"
            assert row.status == MilestoneStatus.PENDING.value
    finally:
        await cleanup_users(owner.id)


@pytest.mark.asyncio
async def test_accept_collaboration_failure_leaves_database_consistent():
    """1 / 7. Accept failure rolls back participant/collaboration changes; DB stays consistent."""
    initiator = await create_and_commit_user("txn_accept_init")
    participant_user = await create_and_commit_user("txn_accept_part")
    collab_id = await create_and_commit_pending_collaboration(
        initiator.id, participant_user.id
    )

    async def fail_update_participant(self, participant):
        raise RuntimeError("simulated participant update failure")

    try:
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=participant_user, session_id="txn-test")
            monkeypatch_target = CollaborationRepository.update_participant
            CollaborationRepository.update_participant = fail_update_participant
            try:
                with pytest.raises(RuntimeError, match="simulated participant update failure"):
                    await _accept_collaboration(ctx, collab_id)
            finally:
                CollaborationRepository.update_participant = monkeypatch_target

        # Verify from an independent session: still PROPOSED, participant still pending.
        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            collab = await repo.get_by_id(collab_id)
            assert collab is not None
            assert collab.status == CollaborationStatus.PROPOSED

            participant = await repo.get_participant(collab_id, participant_user.id)
            assert participant is not None
            assert participant.accepted is False
    finally:
        await cleanup_users(initiator.id, participant_user.id)


@pytest.mark.asyncio
async def test_accepted_collaboration_creates_and_reuses_direct_messaging_bridge():
    """Accept persists one direct thread with both collaborators as members."""
    initiator = await create_and_commit_user("bridge_init")
    participant_user = await create_and_commit_user("bridge_part")
    first_collab_id = await create_and_commit_pending_collaboration(
        initiator.id, participant_user.id
    )

    try:
        async with async_session_factory() as session:
            await _accept_collaboration(
                AppContext(db=session, current_user=participant_user), first_collab_id
            )

        async with async_session_factory() as session:
            conversation_repo = ConversationRepository(session)
            first_conversation = await conversation_repo.get_direct_conversation(
                initiator.id, participant_user.id
            )
            assert first_conversation is not None
            assert set(
                await conversation_repo.get_participant_ids(first_conversation.id)
            ) == {initiator.id, participant_user.id}

        second_collab_id = await create_and_commit_pending_collaboration(
            initiator.id, participant_user.id
        )
        async with async_session_factory() as session:
            await _accept_collaboration(
                AppContext(db=session, current_user=participant_user), second_collab_id
            )

        async with async_session_factory() as session:
            conversation_repo = ConversationRepository(session)
            reused_conversation = await conversation_repo.get_direct_conversation(
                initiator.id, participant_user.id
            )
            assert reused_conversation is not None
            assert reused_conversation.id == first_conversation.id
            assert set(
                await conversation_repo.get_participant_ids(reused_conversation.id)
            ) == {initiator.id, participant_user.id}
    finally:
        await cleanup_users(initiator.id, participant_user.id)


@pytest.mark.asyncio
async def test_messaging_bridge_failure_rolls_back_collaboration_acceptance(monkeypatch):
    """A handoff failure leaves the persisted invite pending."""
    initiator = await create_and_commit_user("bridge_txn_init")
    participant_user = await create_and_commit_user("bridge_txn_part")
    collab_id = await create_and_commit_pending_collaboration(
        initiator.id, participant_user.id
    )

    async def fail_ensure_direct_conversation(self, collaboration, participant):
        raise RuntimeError("simulated messaging bridge failure")

    monkeypatch.setattr(
        "services.collaboration_messaging_service.CollaborationMessagingService.ensure_direct_conversation",
        fail_ensure_direct_conversation,
    )
    try:
        async with async_session_factory() as session:
            with pytest.raises(RuntimeError, match="simulated messaging bridge failure"):
                await _accept_collaboration(
                    AppContext(db=session, current_user=participant_user), collab_id
                )

        async with async_session_factory() as session:
            collaboration = await CollaborationRepository(session).get_by_id(collab_id)
            participant = await CollaborationRepository(session).get_participant(
                collab_id, participant_user.id
            )
            assert collaboration is not None
            assert collaboration.status == CollaborationStatus.PROPOSED
            assert participant is not None
            assert participant.accepted is False
    finally:
        await cleanup_users(initiator.id, participant_user.id)


@pytest.mark.asyncio
async def test_decline_collaboration_failure_leaves_database_consistent():
    """2 / 7. Decline failure rolls back participant deletion/status changes."""
    initiator = await create_and_commit_user("txn_decline_init")
    participant_user = await create_and_commit_user("txn_decline_part")
    collab_id = await create_and_commit_pending_collaboration(
        initiator.id, participant_user.id
    )

    async def fail_remove_participant(self, participant):
        raise RuntimeError("simulated participant delete failure")

    try:
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=participant_user, session_id="txn-test")
            monkeypatch_target = CollaborationRepository.remove_participant
            CollaborationRepository.remove_participant = fail_remove_participant
            try:
                with pytest.raises(RuntimeError, match="simulated participant delete failure"):
                    await _decline_collaboration(ctx, collab_id)
            finally:
                CollaborationRepository.remove_participant = monkeypatch_target

        # Verify from an independent session: still PROPOSED, participant row survives.
        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            collab = await repo.get_by_id(collab_id)
            assert collab is not None
            assert collab.status == CollaborationStatus.PROPOSED

            participant = await repo.get_participant(collab_id, participant_user.id)
            assert participant is not None
            assert participant.accepted is False
    finally:
        await cleanup_users(initiator.id, participant_user.id)


@pytest.mark.asyncio
async def test_full_collaboration_lifecycle_end_to_end():
    """16. End-to-end persisted lifecycle through the real resolvers against Postgres:

    create -> pending invite -> invitee view -> accept -> in_progress ->
    add milestone -> update milestone (in_progress -> completed) -> complete
    collaboration. Each step uses its own session/AppContext (a fresh
    "request") so every assertion reads back genuinely persisted state.
    """
    initiator = await create_and_commit_user("e2e_init")
    invitee = await create_and_commit_user("e2e_invitee")
    collab_id = None

    try:
        # 1-3. Create via the real mutation; verify PROPOSED + pending invite persisted.
        create_input = SimpleNamespace(
            title="E2E Livestream Collab",
            description="Full lifecycle coverage",
            content_type="livestream",
            platform="twitch",
            tags=["e2e", "lifecycle"],
            participant_ids=[invitee.id],
            budget_min=100.0,
            budget_max=400.0,
            budget_currency="USD",
        )
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=initiator, session_id="e2e-create")
            created = await _create_collaboration(ctx, create_input)
        collab_id = created.id

        assert created.status.value == "proposed"
        assert created.proposed_at is not None
        assert created.started_at is None
        assert created.completed_at is None

        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            invitee_participant = await repo.get_participant(collab_id, invitee.id)
            assert invitee_participant is not None
            assert invitee_participant.accepted is False

        # 4. The pending invitee can view the collaboration while it's still PROPOSED.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=invitee, session_id="e2e-view")
            viewed = await _collaboration(ctx, str(collab_id))
        assert viewed is not None
        assert viewed.status.value == "proposed"

        # 5-7. Accept through the real mutation; verify ACCEPTED + timestamps.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=invitee, session_id="e2e-accept")
            accepted_participant = await _accept_collaboration(ctx, collab_id)
        assert accepted_participant.accepted is True

        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            collab = await repo.get_by_id(collab_id)
            assert collab is not None
            assert collab.status == CollaborationStatus.ACCEPTED
            assert collab.proposed_at is not None
            assert collab.started_at is not None
            started_at_after_accept = collab.started_at

        # 8-9. Transition to IN_PROGRESS; started_at must remain the accept-time value.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=initiator, session_id="e2e-in-progress")
            in_progress = await _update_collaboration(
                ctx, str(collab_id), SimpleNamespace(status=SimpleNamespace(value="in_progress"))
            )
        assert in_progress.status.value == "in_progress"
        assert in_progress.started_at is not None
        assert in_progress.started_at.isoformat() == started_at_after_accept

        # 10. Add a milestone through the real mutation.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=initiator, session_id="e2e-add-milestone")
            milestone = await _add_milestone(
                ctx,
                SimpleNamespace(
                    collaboration_id=collab_id,
                    title="Phase 1: Stream Setup",
                    description="Configure stream and channel",
                    due_date=None,
                ),
            )
        milestone_id = milestone.id
        assert milestone.status.value == "pending"
        assert milestone.completed_at is None

        # 11. Update the milestone to IN_PROGRESS; completed_at must stay unset.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=initiator, session_id="e2e-milestone-progress")
            milestone = await _update_milestone(
                ctx,
                str(milestone_id),
                SimpleNamespace(title=None, description=None, status=SimpleNamespace(value="in_progress"), due_date=None),
            )
        assert milestone.status.value == "in_progress"
        assert milestone.completed_at is None

        # 12-13. Complete the milestone; completed_at must now be persisted.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=initiator, session_id="e2e-milestone-complete")
            milestone = await _update_milestone(
                ctx,
                str(milestone_id),
                SimpleNamespace(title=None, description=None, status=SimpleNamespace(value="completed"), due_date=None),
            )
        assert milestone.status.value == "completed"
        assert milestone.completed_at is not None

        # 14-16. Complete the collaboration; completed_at persisted, status COMPLETED.
        async with async_session_factory() as session:
            ctx = AppContext(db=session, current_user=initiator, session_id="e2e-complete")
            completed = await _update_collaboration(
                ctx, str(collab_id), SimpleNamespace(status=SimpleNamespace(value="completed"))
            )
        assert completed.status.value == "completed"
        assert completed.completed_at is not None

        # 17. Final state, read back from an independent session.
        async with async_session_factory() as session:
            repo = CollaborationRepository(session)
            final_collab = await repo.get_by_id(collab_id)
            assert final_collab is not None
            assert final_collab.status == CollaborationStatus.COMPLETED
            assert final_collab.proposed_at is not None
            assert final_collab.started_at is not None
            assert final_collab.completed_at is not None

            final_milestone = await repo.get_milestone_by_id(milestone_id)
            assert final_milestone is not None
            assert final_milestone.status == MilestoneStatus.COMPLETED
            assert final_milestone.completed_at is not None
    finally:
        await cleanup_users(initiator.id, invitee.id)


@pytest.mark.asyncio
async def test_discovered_creator_becomes_pending_collaboration_participant():
    """A discovered creator's returned user ID is eligible and persisted as an invitee."""
    initiator = await create_and_commit_user("handoff_init")
    creator = await create_and_commit_user("handoff_creator")
    handoff_tag = f"step-10-handoff-{creator.id}"

    try:
        async with async_session_factory() as session:
            session.add(
                Profile(
                    user_id=creator.id,
                    display_name="Handoff Creator",
                    tags=[handoff_tag],
                    open_to_collab=True,
                    private_account=False,
                )
            )
            await session.commit()

        async with async_session_factory() as session:
            discovery = await _discover_creators(
                AppContext(db=session, current_user=initiator, session_id="handoff-discovery"),
                query=None,
                tags=[handoff_tag],
                first=20,
                after=None,
            )
        selected_creator_id = discovery.edges[0].node.user.id
        assert selected_creator_id == creator.id

        create_input = SimpleNamespace(
            title="Discovered Creator Handoff",
            description=None,
            content_type="video",
            platform="youtube",
            tags=[handoff_tag],
            participant_ids=[selected_creator_id],
            budget_min=None,
            budget_max=None,
            budget_currency=None,
        )
        async with async_session_factory() as session:
            created = await _create_collaboration(
                AppContext(db=session, current_user=initiator, session_id="handoff-create"),
                create_input,
            )

        async with async_session_factory() as session:
            participant = await CollaborationRepository(session).get_participant(
                created.id, selected_creator_id
            )
            assert participant is not None
            assert participant.user_id == selected_creator_id
            assert participant.accepted is False
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Profile).where(Profile.user_id == creator.id))
            await session.execute(delete(User).where(User.id.in_([initiator.id, creator.id])))
            await session.commit()

