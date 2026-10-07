"""Real PostgreSQL transactions; opt in with PAID_TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, Table, delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.graphql as graphql
from api.graphql import AppContext
from app.models.analytics import AnalyticsEvent, EventType, InteractionSignal, SignalType
from app.models.content import Comment, ContentStatus, Media, Post
from app.models.paid_campaign import PaidCampaign, PaidCampaignStatus
from app.models.paid_delivery import PaidDelivery, PaidInteractionContext
from app.models.social import PostLike, PostSave, PostShare, PostWatch, UserBlock, UserMute
from app.models.streaming import StreamSession
from app.models.user import AccountStatus, Profile, Session, User
from repositories.analytics_event_repository import AnalyticsEventRepository
from repositories.analytics_repository import AnalyticsRepository
from repositories.paid_delivery_repository import PaidDeliveryRepository
from repositories.paid_impression_history_repository import (
    PaidImpressionHistoryRepository,
    PaidImpressionHistoryUnavailable,
)
from repositories.social_repository import PostInteractionRepository
from services.analytics_event_service import AnalyticsEventService
from services.paid_campaign_service import PaidCampaignService, PaidCandidate
from services.paid_delivery_service import PaidDeliveryService
from services.paid_frequency import FrequencyStatus
from services.paid_interaction_context_service import PaidInteractionContextService


@pytest_asyncio.fixture
async def store():
    url = os.environ.get("PAID_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PAID_TEST_DATABASE_URL to an isolated PostgreSQL database")
    schema = f"paid_accounting_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    metadata = MetaData()
    for model in (
        User,
        Profile,
        Session,
        Post,
        Comment,
        Media,
        PaidCampaign,
        PaidDelivery,
        PaidInteractionContext,
        UserBlock,
        UserMute,
        AnalyticsEvent,
        InteractionSignal,
        StreamSession,
        PostLike,
        PostSave,
        PostShare,
        PostWatch,
    ):
        table = model.__table__
        assert isinstance(table, Table)
        table.to_metadata(metadata)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield sessions, schema, metadata
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def setup_campaign(store, **changes):
    sessions, _, _ = store
    async with sessions() as db:
        users = [
            User(
                username=uuid.uuid4().hex,
                email=f"{uuid.uuid4().hex}@example.test",
                hashed_password="hashed",
                status=AccountStatus.ACTIVE,
            )
            for _ in range(3)
        ]
        db.add_all(users)
        await db.flush()
        post = Post(
            user_id=users[0].id,
            status=ContentStatus.PUBLISHED,
            visibility="public",
            moderation_status="approved",
        )
        db.add(post)
        await db.flush()
        now = datetime.now(timezone.utc)
        values = dict(
            owner_id=users[0].id,
            post_id=post.id,
            status=PaidCampaignStatus.ACTIVE,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            budget_minor_units=1000,
            spent_minor_units=0,
            currency="USD",
            impressions_delivered=0,
            reach_delivered=0,
            max_impressions=100,
            max_reach=100,
        )
        values.update(changes)
        campaign = PaidCampaign(**values)
        db.add(campaign)
        await db.commit()
        return campaign.id, post.id, users[1].id, users[2].id


async def selection(store, campaign_id, post_id, viewer_id):
    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        assert viewer is not None
        identity = await PaidDeliveryService(db).select_candidate(
            viewer, PaidCandidate(post_id=post_id, paid_campaign_id=campaign_id)
        )
        await db.commit()
        return identity


async def impression(store, delivery_id, viewer_id):
    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        assert viewer is not None
        result = await PaidDeliveryService(db).record_impression(viewer, delivery_id)
        await db.commit()
        return result


async def counts(store, campaign_id):
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        assert campaign is not None
        delivered = await db.scalar(
            select(func.count())
            .select_from(PaidDelivery)
            .where(PaidDelivery.campaign_id == campaign_id, PaidDelivery.impressed_at.is_not(None))
        )
        return (
            campaign.impressions_delivered,
            campaign.reach_delivered,
            delivered,
            campaign.spent_minor_units,
        )


@pytest.mark.asyncio
async def test_authoritative_selection_attribution_dedup_reach_and_analytics_isolation(store):
    campaign_id, post_id, viewer_id, second_viewer = await setup_campaign(store)
    first = await selection(store, campaign_id, post_id, viewer_id)
    assert await counts(store, campaign_id) == (0, 0, 0, 0)
    receipt = await impression(store, first, viewer_id)
    assert receipt.delivery_id == first and receipt.paid_campaign_id == campaign_id
    assert receipt.post_id == post_id and receipt.source == "PAID"
    assert receipt.is_sponsored and receipt.sponsored_label == "Sponsored"
    assert not hasattr(receipt, "budget_minor_units")
    assert await impression(store, first, viewer_id) == receipt
    assert await counts(store, campaign_id) == (1, 1, 1, 0)
    later = await selection(store, campaign_id, post_id, viewer_id)
    assert later != first
    await impression(store, later, viewer_id)
    await impression(
        store, await selection(store, campaign_id, post_id, second_viewer), second_viewer
    )
    assert await counts(store, campaign_id) == (3, 2, 3, 0)
    async with store[0]() as db:
        assert await db.scalar(select(func.count()).select_from(AnalyticsEvent)) == 0
        assert await db.scalar(select(func.count()).select_from(InteractionSignal)) == 0
        campaign = await db.get(PaidCampaign, campaign_id)
        campaign.status = PaidCampaignStatus.PAUSED
        await db.commit()
    # A retry is a historical receipt, not new delivery, even after pausing.
    assert await impression(store, first, viewer_id) == receipt
    assert await counts(store, campaign_id) == (3, 2, 3, 0)


@pytest.mark.asyncio
async def test_forged_selection_wrong_viewer_and_wrong_post_are_rejected(store):
    campaign_id, post_id, viewer_id, other_viewer = await setup_campaign(store)
    identity = await selection(store, campaign_id, post_id, viewer_id)
    for forged, viewer in (
        (uuid.uuid4(), viewer_id),
        (campaign_id, viewer_id),
        (identity, other_viewer),
    ):
        with pytest.raises(ValueError, match="selection not found"):
            await impression(store, forged, viewer)
    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        with pytest.raises(ValueError, match="post does not match"):
            await PaidDeliveryService(db).select_candidate(
                viewer, PaidCandidate(post_id=uuid.uuid4(), paid_campaign_id=campaign_id)
            )
        with pytest.raises(ValueError, match="campaign not found"):
            await PaidDeliveryService(db).select_candidate(
                viewer, PaidCandidate(post_id=post_id, paid_campaign_id=uuid.uuid4())
            )
        with pytest.raises(TypeError):
            await PaidDeliveryService(db).record_impression(
                viewer, identity, **{"source": "PAID", "campaign_id": campaign_id}
            )
        await db.commit()
    assert await counts(store, campaign_id) == (0, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"status": status} for status in PaidCampaignStatus if status != PaidCampaignStatus.ACTIVE]
    + [
        {
            "start_at": datetime.now(timezone.utc) + timedelta(days=1),
            "end_at": datetime.now(timezone.utc) + timedelta(days=2),
        },
        {"end_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"spent_minor_units": 1000},
        {"impressions_delivered": 100},
        {"impressions_delivered": 100, "reach_delivered": 100},
    ],
)
async def test_campaign_gates_prevent_new_selection(store, change):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store, **change)
    with pytest.raises(ValueError, match="not eligible"):
        await selection(store, campaign_id, post_id, viewer_id)
    async with store[0]() as db:
        assert await db.scalar(select(func.count()).select_from(PaidDelivery)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate",
    [
        "paused",
        "expiry",
        "budget",
        "post",
        "viewer",
        "targeting",
        "block",
        "mute",
        "moderation",
        "deleted_post",
        "deleted_owner",
        "private_profile",
        "reverse_block",
    ],
)
async def test_pending_selection_is_revalidated_before_accounting(store, gate):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    identity = await selection(store, campaign_id, post_id, viewer_id)
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        if gate == "paused":
            campaign.status = PaidCampaignStatus.PAUSED
        elif gate == "expiry":
            campaign.end_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif gate == "budget":
            campaign.spent_minor_units = campaign.budget_minor_units
        elif gate == "post":
            (await db.get(Post, post_id)).visibility = "private"
        elif gate == "viewer":
            (await db.get(User, viewer_id)).status = AccountStatus.SUSPENDED
        elif gate == "targeting":
            campaign.targeting = {"tags": ["music"]}
        elif gate == "block":
            db.add(UserBlock(blocker_id=viewer_id, blocked_id=campaign.owner_id))
        elif gate == "mute":
            db.add(UserMute(muter_id=viewer_id, muted_id=campaign.owner_id))
        elif gate == "moderation":
            (await db.get(Post, post_id)).moderation_status = "rejected"
        elif gate == "deleted_post":
            (await db.get(Post, post_id)).deleted_at = datetime.now(timezone.utc)
        elif gate == "deleted_owner":
            (await db.get(User, campaign.owner_id)).deleted_at = datetime.now(timezone.utc)
        elif gate == "private_profile":
            db.add(Profile(user_id=campaign.owner_id, display_name="owner", private_account=True))
        else:
            db.add(UserBlock(blocker_id=campaign.owner_id, blocked_id=viewer_id))
        await db.commit()
    with pytest.raises((ValueError, PermissionError)):
        await impression(store, identity, viewer_id)
    expected_spend = 1000 if gate == "budget" else 0
    assert await counts(store, campaign_id) == (0, 0, 0, expected_spend)
    async with store[0]() as db:
        assert (await db.get(PaidDelivery, identity)).impressed_at is None


@pytest.mark.asyncio
async def test_frequency_authoritative_window_pending_and_complete_history(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(
        store, frequency_cap=2, frequency_window_seconds=3600
    )
    pending = await selection(store, campaign_id, post_id, viewer_id)
    first = await selection(store, campaign_id, post_id, viewer_id)
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        viewer = await db.get(User, viewer_id)
        eligibility = await PaidCampaignService(db).evaluate_eligibility(campaign, viewer)
        assert eligibility.eligible and eligibility.frequency.impressions == 0
    await impression(store, first, viewer_id)
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        viewer = await db.get(User, viewer_id)
        assert (await PaidCampaignService(db).evaluate_eligibility(campaign, viewer)).eligible
        exact = (await db.get(PaidDelivery, first)).impressed_at
        assert (
            await PaidImpressionHistoryRepository(db).count_paid_impressions(
                viewer_id=viewer_id, campaign_id=campaign_id, start_at=exact, end_at=exact
            )
            == 1
        )
        assert (
            await PaidImpressionHistoryRepository(db).count_paid_impressions(
                viewer_id=viewer_id,
                campaign_id=campaign_id,
                start_at=exact + timedelta(microseconds=1),
                end_at=exact + timedelta(seconds=1),
            )
            == 0
        )
    await impression(store, pending, viewer_id)
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        viewer = await db.get(User, viewer_id)
        eligibility = await PaidCampaignService(db).evaluate_eligibility(campaign, viewer)
        assert not eligibility.eligible and eligibility.frequency.status == FrequencyStatus.CAPPED
    with pytest.raises(ValueError, match="frequency"):
        await selection(store, campaign_id, post_id, viewer_id)
    assert await counts(store, campaign_id) == (2, 1, 2, 0)


@pytest.mark.asyncio
async def test_repeated_paid_feed_fetches_do_not_record_impressions(store, monkeypatch):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(
        store, frequency_cap=1, frequency_window_seconds=3600
    )
    monkeypatch.setattr(graphql, "PAID_PUBLIC_DELIVERY_ENABLED", True)

    async def feed_item(_ctx, post):
        return SimpleNamespace(id=post.id)

    monkeypatch.setattr(graphql, "_post_to_feed_item", feed_item)
    returned_ids = []
    for _ in range(2):
        async with store[0]() as db:
            viewer = await db.get(User, viewer_id)
            assert viewer is not None
            page = await graphql._paid_feed(AppContext(db, viewer), viewer, [], None, 10, None)
            assert len(page.items) == 1
            returned_ids.append(page.items[0].paid_delivery_id)

    assert returned_ids[0] != returned_ids[1]
    assert await counts(store, campaign_id) == (0, 0, 0, 0)
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        viewer = await db.get(User, viewer_id)
        assert campaign is not None and viewer is not None
        eligibility = await PaidCampaignService(db).evaluate_eligibility(campaign, viewer)
        assert eligibility.eligible
        assert eligibility.frequency.impressions == 0
        selections = list(
            (
                await db.scalars(
                    select(PaidDelivery).where(PaidDelivery.campaign_id == campaign_id)
                )
            ).all()
        )
        assert len(selections) == 2
        assert all(selection.impressed_at is None for selection in selections)


@pytest.mark.asyncio
async def test_paid_interaction_and_non_paid_replay_have_distinct_provenance(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    delivery_id = await selection(store, campaign_id, post_id, viewer_id)
    await impression(store, delivery_id, viewer_id)

    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        assert viewer is not None
        context = await PaidInteractionContextService(db).issue(viewer_id, delivery_id)
        forged_context = "forged-paid-interaction-context"
        forged_result = await graphql.schema.execute(
            """
            mutation Forged($id: UUID!, $delivery: UUID!, $context: String!) {
              paidSavePost(
                id: $id, paidDeliveryId: $delivery, paidInteractionContext: $context
              ) { saved saves }
            }
            """,
            variable_values={
                "id": str(post_id),
                "delivery": str(delivery_id),
                "context": forged_context,
            },
            context_value=AppContext(db, viewer),
        )
        assert forged_result.data is None
        assert forged_result.errors

        missing_context_result = await graphql.schema.execute(
            """
            mutation Missing($id: UUID!, $delivery: UUID!) {
              paidSavePost(id: $id, paidDeliveryId: $delivery) { saved saves }
            }
            """,
            variable_values={"id": str(post_id), "delivery": str(delivery_id)},
            context_value=AppContext(db, viewer),
        )
        assert missing_context_result.data is None
        assert missing_context_result.errors

        paid_result = await graphql.schema.execute(
            """
            mutation PaidSave($id: UUID!, $delivery: UUID!, $context: String!) {
              paidSavePost(
                id: $id, paidDeliveryId: $delivery, paidInteractionContext: $context
              ) { saved saves }
            }
            """,
            variable_values={
                "id": str(post_id),
                "delivery": str(delivery_id),
                "context": context,
            },
            context_value=AppContext(db, viewer),
        )
        assert paid_result.errors is None
        assert paid_result.data == {"paidSavePost": {"saved": True, "saves": 1}}

        paid_save = await db.scalar(
            select(PostSave).where(PostSave.post_id == post_id, PostSave.user_id == viewer_id)
        )
        assert paid_save is not None
        assert paid_save.paid_delivery_id == delivery_id
        assert paid_save.paid_campaign_id == campaign_id

        reused_context_result = await graphql.schema.execute(
            """
            mutation Reused($id: UUID!, $delivery: UUID!, $context: String!) {
              paidSharePost(
                id: $id, paidDeliveryId: $delivery, paidInteractionContext: $context
              ) { shared shares }
            }
            """,
            variable_values={
                "id": str(post_id),
                "delivery": str(delivery_id),
                "context": context,
            },
            context_value=AppContext(db, viewer),
        )
        assert reused_context_result.data is None
        assert reused_context_result.errors

        rejected_replay = await graphql.schema.execute(
            """
            mutation Replay($id: UUID!, $delivery: UUID!) {
              savePost(id: $id, paidDeliveryId: $delivery) { saved saves }
            }
            """,
            variable_values={"id": str(post_id), "delivery": str(delivery_id)},
            context_value=AppContext(db, viewer),
        )
        assert rejected_replay.data is None
        replay_errors = rejected_replay.errors
        assert replay_errors is not None
        assert len(replay_errors) > 0
        assert "Unknown argument 'paidDeliveryId'" in replay_errors[0].message

        normal_result = await graphql.schema.execute(
            """
            mutation NormalShare($id: UUID!) {
              sharePost(id: $id) { shared shares }
            }
            """,
            variable_values={"id": str(post_id)},
            context_value=AppContext(db, viewer),
        )
        assert normal_result.errors is None
        assert normal_result.data == {"sharePost": {"shared": True, "shares": 1}}

        normal_share = await db.scalar(
            select(PostShare).where(PostShare.post_id == post_id, PostShare.user_id == viewer_id)
        )
        assert normal_share is not None
        assert normal_share.paid_delivery_id is None
        assert normal_share.paid_campaign_id is None

        signals = list(
            (
                await db.scalars(
                    select(InteractionSignal)
                    .where(InteractionSignal.user_id == viewer_id)
                    .order_by(InteractionSignal.created_at)
                )
            ).all()
        )
        assert [(signal.signal_type, signal.paid_delivery_id) for signal in signals] == [
            (SignalType.SAVE, delivery_id),
            (SignalType.SHARE, None),
        ]


@pytest.mark.asyncio
async def test_incomplete_and_missing_schema_fail_closed_without_poisoning_transaction(store):
    campaign_id, _, viewer_id, _ = await setup_campaign(
        store,
        frequency_cap=1,
        frequency_window_seconds=60,
        impressions_delivered=1,
        reach_delivered=1,
    )
    async with store[0]() as db:
        campaign = await db.get(PaidCampaign, campaign_id)
        viewer = await db.get(User, viewer_id)
        result = await PaidCampaignService(db).evaluate_eligibility(campaign, viewer)
        assert result.frequency.status == FrequencyStatus.ATTRIBUTION_UNAVAILABLE
        with pytest.raises(PaidImpressionHistoryUnavailable, match="incomplete"):
            await PaidImpressionHistoryRepository(db).count_paid_impressions(
                viewer_id=viewer_id,
                campaign_id=campaign_id,
                start_at=campaign.start_at,
                end_at=datetime.now(timezone.utc),
            )
        await db.execute(text("DROP TABLE paid_deliveries"))
        result = await PaidCampaignService(db).evaluate_eligibility(campaign, viewer)
        assert result.frequency.status == FrequencyStatus.ATTRIBUTION_UNAVAILABLE
        assert await db.scalar(select(func.count()).select_from(User)) == 3
        await db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["impressions", "reach", "same_viewer", "different_viewers", "same_id", "frequency"]
)
async def test_concurrent_accounting_is_serialized_and_caps_cannot_overshoot(store, mode):
    limits = {}
    if mode == "impressions":
        limits["max_impressions"] = 1
    if mode == "reach":
        limits["max_reach"] = 1
    if mode == "frequency":
        limits.update(frequency_cap=1, frequency_window_seconds=3600)
    campaign_id, post_id, viewer_id, second_viewer = await setup_campaign(store, **limits)
    first = await selection(store, campaign_id, post_id, viewer_id)
    second_user = second_viewer if mode in {"different_viewers", "reach"} else viewer_id
    second = (
        first if mode == "same_id" else await selection(store, campaign_id, post_id, second_user)
    )
    ready = asyncio.Barrier(2)

    async def record(identity, user_id):
        async with store[0]() as db:
            # Preload stale campaign state before either contender accounts.
            campaign = await db.get(PaidCampaign, campaign_id)
            viewer = await db.get(User, user_id)
            await ready.wait()
            try:
                receipt = await PaidDeliveryService(db).record_impression(viewer, identity)
                await db.commit()
                assert campaign.impressions_delivered >= 1
                return receipt
            except ValueError as exc:
                await db.rollback()
                return exc

    tasks = [
        asyncio.create_task(record(first, viewer_id)),
        asyncio.create_task(record(second, second_user)),
    ]
    results = await asyncio.gather(*tasks)
    failures = sum(isinstance(result, ValueError) for result in results)
    if mode in {"impressions", "reach", "frequency"}:
        assert failures == 1
        assert await counts(store, campaign_id) == (1, 1, 1, 0)
    elif mode == "same_id":
        assert failures == 0
        assert results[0] == results[1]
        assert await counts(store, campaign_id) == (1, 1, 1, 0)
    else:
        assert failures == 0
        assert await counts(store, campaign_id) == (
            2,
            2 if mode == "different_viewers" else 1,
            2,
            0,
        )


@pytest.mark.asyncio
async def test_deleted_viewer_does_not_erase_authoritative_history_or_reach(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    await impression(store, await selection(store, campaign_id, post_id, viewer_id), viewer_id)
    async with store[0]() as db:
        await db.execute(delete(User).where(User.id == viewer_id))
        await db.commit()
        now = datetime.now(timezone.utc)
        assert (
            await PaidImpressionHistoryRepository(db).count_paid_impressions(
                viewer_id=viewer_id,
                campaign_id=campaign_id,
                start_at=now - timedelta(hours=1),
                end_at=now,
            )
            == 1
        )
    assert await counts(store, campaign_id) == (1, 1, 1, 0)


@pytest.mark.asyncio
async def test_uncapped_accounting_rejects_incomplete_lifetime_history(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(
        store, impressions_delivered=1, reach_delivered=1
    )
    with pytest.raises(PaidImpressionHistoryUnavailable, match="incomplete"):
        await selection(store, campaign_id, post_id, viewer_id)
    assert await counts(store, campaign_id) == (1, 1, 0, 0)


@pytest.mark.asyncio
async def test_accounting_refreshes_preloaded_viewer_targeting_profile(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store, targeting={"tags": ["music"]})
    async with store[0]() as db:
        db.add(Profile(user_id=viewer_id, display_name="viewer", tags=["music"]))
        await db.commit()
    identity = await selection(store, campaign_id, post_id, viewer_id)
    async with store[0]() as stale:
        profile = (await stale.scalars(select(Profile).where(Profile.user_id == viewer_id))).one()
        viewer = await stale.get(User, viewer_id)
        assert profile.tags == ["music"]
        async with store[0]() as writer:
            fresh = (
                await writer.scalars(select(Profile).where(Profile.user_id == viewer_id))
            ).one()
            fresh.tags = []
            await writer.commit()
        with pytest.raises(ValueError, match="targeting"):
            await PaidDeliveryService(stale).record_impression(viewer, identity)
        await stale.commit()
    assert await counts(store, campaign_id) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_paid_attribution_is_persisted_queryable_and_excluded_from_recommendations(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    delivery_id = await selection(store, campaign_id, post_id, viewer_id)
    await impression(store, delivery_id, viewer_id)

    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        post = await db.get(Post, post_id)
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.VIDEO_VIEWED,
            user=viewer,
            post=post,
            metadata={
                "source": "paid",
                "algorithm": "PAID",
                "isSponsored": True,
                "paid_campaign_id": str(uuid.uuid4()),
            },
            paid_delivery_id=delivery_id,
        )
        assert event is not None
        assert event.paid_delivery_id == delivery_id
        assert event.paid_campaign_id == campaign_id
        assert event.event_metadata["source"] == "paid"
        assert event.event_metadata["paid_delivery_id"] == str(delivery_id)

        signal = await AnalyticsRepository(db).record(
            user_id=viewer_id,
            creator_id=post.user_id,
            post_id=post_id,
            signal_type=SignalType.LIKE,
            paid_delivery_id=delivery_id,
        )
        assert signal.paid_delivery_id == delivery_id
        assert signal.paid_campaign_id == campaign_id

        active_like = await PostInteractionRepository(db).toggle_like(
            post_id, viewer_id, paid_delivery_id=delivery_id
        )
        assert active_like
        active = await db.execute(
            select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == viewer_id)
        )
        assert active.scalar_one().paid_delivery_id == delivery_id

        events = await AnalyticsEventRepository(db).get_for_paid_campaign(campaign_id)
        assert event in events

        affinity = await AnalyticsRepository(db).creator_affinity(viewer_id)
        history = await AnalyticsRepository(db).viewer_post_history(viewer_id, [post_id])
        rates = await AnalyticsRepository(db).post_engagement_rates([post_id])
        momentum = await AnalyticsRepository(db).recent_post_engagement(
            [post_id], datetime.now(timezone.utc) - timedelta(days=1)
        )
        assert affinity == []
        assert history == {}
        assert rates == {}
        assert momentum == {}


@pytest.mark.asyncio
async def test_paid_receipt_does_not_claim_an_unrelated_non_paid_surface_engagement(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    first = await selection(store, campaign_id, post_id, viewer_id)
    await impression(store, first, viewer_id)
    second = await selection(store, campaign_id, post_id, viewer_id)
    await impression(store, second, viewer_id)

    async with store[0]() as db:
        latest = await PaidDeliveryRepository(db).latest_impressed_for_engagement(
            viewer_id, post_id
        )
        assert latest is not None and latest.id == second

        viewer = await db.get(User, viewer_id)
        post = await db.get(Post, post_id)
        non_paid_event = await AnalyticsEventService(db).track_event(
            event_type=EventType.LIKE_CREATED,
            user=viewer,
            post=post,
            metadata={"source": "for_you_feed"},
        )
        assert non_paid_event is not None
        assert non_paid_event.paid_delivery_id is None
        assert non_paid_event.paid_campaign_id is None
        assert non_paid_event.event_metadata == {"source": "for_you_feed"}
        signal = await AnalyticsRepository(db).record(
            user_id=viewer_id,
            creator_id=post.user_id,
            post_id=post_id,
            signal_type=SignalType.LIKE,
        )
        assert signal.paid_delivery_id is None
        assert signal.paid_campaign_id is None
        await PostInteractionRepository(db).toggle_like(post_id, viewer_id)
        active = await db.execute(
            select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == viewer_id)
        )
        assert active.scalar_one().paid_delivery_id is None


@pytest.mark.asyncio
async def test_missing_paid_receipt_does_not_infer_attribution_from_client_claims(store):
    _, post_id, viewer_id, _ = await setup_campaign(store)

    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        post = await db.get(Post, post_id)
        event = await AnalyticsEventService(db).track_event(
            event_type=EventType.VIDEO_VIEWED,
            user=viewer,
            post=post,
            metadata={
                "source": "PAID",
                "algorithm": "PAID",
                "isSponsored": True,
                "paidCampaignId": str(uuid.uuid4()),
                "paidDeliveryId": str(uuid.uuid4()),
            },
        )
        assert event is not None
        assert event.paid_delivery_id is None
        assert event.paid_campaign_id is None
        assert event.event_metadata is None


@pytest.mark.asyncio
async def test_migrations_208_through_210_upgrade_and_downgrade_on_postgres(store):
    _, schema, _ = store
    root = Path(__file__).resolve().parents[2]
    migration_208 = root / "backend" / "alembic" / "versions" / "208_paid_delivery_accounting.py"
    migration_209 = root / "backend" / "alembic" / "versions" / "209_paid_engagement_attribution.py"
    migration_210 = root / "backend" / "alembic" / "versions" / "210_paid_interaction_context.py"
    script = """
import asyncio, importlib.util, sys
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
m208 = load(sys.argv[1], "paid_migration_208")
m209 = load(sys.argv[2], "paid_migration_209")
m210 = load(sys.argv[3], "paid_migration_210")
async def run():
    engine = create_async_engine(sys.argv[4], connect_args={"server_settings": {"search_path": sys.argv[5]}})
    async with engine.begin() as conn:
        def check(sync):
            with Operations.context(MigrationContext.configure(sync)):
                m210.downgrade()
                assert not inspect(sync).has_table("paid_interaction_contexts")
                m209.downgrade()
                assert "paid_delivery_id" not in {c["name"] for c in inspect(sync).get_columns("analytics_events")}
                m208.downgrade()
                assert not inspect(sync).has_table("paid_deliveries")
                m208.upgrade()
                assert inspect(sync).has_table("paid_deliveries")
                m209.upgrade()
                m210.upgrade()
                assert inspect(sync).has_table("paid_interaction_contexts")
                for table in ("interaction_signals", "analytics_events", "post_likes", "post_saves", "post_shares", "post_watches"):
                    columns = {c["name"] for c in inspect(sync).get_columns(table)}
                    assert {"paid_delivery_id", "paid_campaign_id"} <= columns
                    checks = {c["name"] for c in inspect(sync).get_check_constraints(table)}
                    assert f"ck_{table}_paid_attribution" in checks
                indexes = {i["name"] for i in inspect(sync).get_indexes("analytics_events")}
                assert "ix_analytics_events_paid_campaign_created" in indexes
                indexes = {i["name"] for i in inspect(sync).get_indexes("paid_deliveries")}
                assert "ix_paid_deliveries_campaign_viewer_time" in indexes
                indexes = {i["name"] for i in inspect(sync).get_indexes("paid_interaction_contexts")}
                assert "ix_paid_interaction_context_expiry" in indexes
        await conn.run_sync(check)
    await engine.dispose()
asyncio.run(run())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            script,
            str(migration_208),
            str(migration_209),
            str(migration_210),
            os.environ["PAID_TEST_DATABASE_URL"],
            schema,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_accounting_flush_failure_rolls_back_ledger_and_counters(store, monkeypatch):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    identity = await selection(store, campaign_id, post_id, viewer_id)
    async with store[0]() as db:
        viewer = await db.get(User, viewer_id)
        service = PaidDeliveryService(db)
        original = service.repository.record_locked

        async def fail_after_writes(*args):
            await original(*args)
            raise RuntimeError("failure after atomic writes")

        monkeypatch.setattr(service.repository, "record_locked", fail_after_writes)
        with pytest.raises(RuntimeError, match="failure after atomic writes"):
            await service.record_impression(viewer, identity)
        await db.commit()
    assert await counts(store, campaign_id) == (0, 0, 0, 0)
    await impression(store, identity, viewer_id)
    assert await counts(store, campaign_id) == (1, 1, 1, 0)


@pytest.mark.asyncio
async def test_caller_rollback_discards_successful_accounting(store):
    campaign_id, post_id, viewer_id, _ = await setup_campaign(store)
    identity = await selection(store, campaign_id, post_id, viewer_id)
    async with store[0]() as db:
        await PaidDeliveryService(db).record_impression(await db.get(User, viewer_id), identity)
        await db.rollback()
    assert await counts(store, campaign_id) == (0, 0, 0, 0)
    await impression(store, identity, viewer_id)
    assert await counts(store, campaign_id) == (1, 1, 1, 0)


@pytest.mark.asyncio
async def test_migration_208_upgrade_downgrade_on_postgres_matches_model(store):
    _, schema, metadata = store
    root = Path(__file__).resolve().parents[2]
    migration = root / "backend" / "alembic" / "versions" / "208_paid_delivery_accounting.py"
    script = """
import asyncio, importlib.util, sys
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
spec = importlib.util.spec_from_file_location("paid_migration", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
async def run():
    engine = create_async_engine(sys.argv[2], connect_args={"server_settings": {"search_path": sys.argv[3]}})
    async with engine.begin() as conn:
        def check(sync):
            with Operations.context(MigrationContext.configure(sync)):
                m.downgrade()
                assert not inspect(sync).has_table("paid_deliveries")
                m.upgrade()
                assert inspect(sync).has_table("paid_deliveries")
                columns = {c["name"]: c for c in inspect(sync).get_columns("paid_deliveries")}
                assert columns["impressed_at"]["nullable"]
                assert not columns["selected_at"]["nullable"]
                assert {"ck_paid_deliveries_time"} == {
                    c["name"] for c in inspect(sync).get_check_constraints("paid_deliveries")
                }
                assert {("campaign_id", "paid_campaigns", "CASCADE"), ("post_id", "posts", "CASCADE")} == {
                    (fk["constrained_columns"][0], fk["referred_table"], fk["options"]["ondelete"])
                    for fk in inspect(sync).get_foreign_keys("paid_deliveries")
                }
                sync.execute(text("INSERT INTO paid_deliveries (id, campaign_id, viewer_id, post_id, selected_at) SELECT gen_random_uuid(), id, gen_random_uuid(), post_id, clock_timestamp() FROM paid_campaigns"))
        await conn.run_sync(check)
    await engine.dispose()
asyncio.run(run())
"""
    campaign_id, _, _, _ = await setup_campaign(store)
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            script,
            str(migration),
            os.environ["PAID_TEST_DATABASE_URL"],
            schema,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    async with store[0]() as db:
        row = (await db.execute(select(PaidDelivery))).scalar_one()
        assert row.campaign_id == campaign_id and row.impressed_at is None
        columns = (
            (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'paid_deliveries'"
                    ),
                    {"schema": schema},
                )
            )
            .scalars()
            .all()
        )
        assert set(columns) == set(metadata.tables["paid_deliveries"].columns.keys())
        indexes = (
            (
                await db.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND tablename = 'paid_deliveries'"
                    ),
                    {"schema": schema},
                )
            )
            .scalars()
            .all()
        )
        assert {index.name for index in metadata.tables["paid_deliveries"].indexes} <= set(indexes)
