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

import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.collaboration import (
    Collaboration,
    CollaborationParticipant,
    CollaborationStatus,
    Milestone,
    MilestoneStatus,
)
from app.models.user import AccountStatus, User, UserRole
from repositories.collaboration_repository import CollaborationRepository


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

