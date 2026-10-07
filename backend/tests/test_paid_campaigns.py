"""Paid domain/database/API foundation, without enabling campaign delivery."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from graphql import GraphQLObjectType
from sqlalchemy import JSON, MetaData, Table, Uuid, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.graphql import AppContext, FeedAlgorithm, FeedItemType, _feed, schema
from app.models.analytics import AnalyticsEvent, EventType
from app.models.content import Comment, ContentStatus, Media, Post
from app.models.paid_campaign import PaidCampaign, PaidCampaignStatus
from app.models.paid_delivery import PaidDelivery
from app.models.social import UserBlock, UserMute
from app.models.user import AccountStatus, Profile, Session, User, UserRole
from repositories.content_repository import PostRepository
from repositories.paid_campaign_repository import PaidCampaignRepository
from repositories.paid_impression_history_repository import (
    PaidImpressionHistoryUnavailable,
)
from services.paid_campaign_service import (
    CampaignConfiguration,
    PaidCampaignService,
    campaign_is_active,
    campaign_is_exhausted,
    campaign_is_expired,
)
from services.paid_delivery_service import PaidDeliveryService
from services.paid_frequency import FrequencyStatus, PaidFrequencyService
from services.paid_targeting import PAID_TARGET_TAGS, targeting_matches, validate_targeting

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
CONFIG = CampaignConfiguration(
    start_at=NOW - timedelta(hours=1),
    end_at=NOW + timedelta(days=1),
    budget_minor_units=10_000,
    currency="USD",
    max_impressions=100,
    max_reach=50,
)


def campaign(**changes):
    values = dict(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        status=PaidCampaignStatus.ACTIVE,
        start_at=CONFIG.start_at,
        end_at=CONFIG.end_at,
        budget_minor_units=CONFIG.budget_minor_units,
        currency="USD",
        spent_minor_units=0,
        impressions_delivered=0,
        reach_delivered=0,
        max_impressions=100,
        max_reach=50,
    )
    values.update(changes)
    return PaidCampaign(**values)


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'paid.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

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
        UserBlock,
        UserMute,
        AnalyticsEvent,
    ):
        table = model.__table__
        assert isinstance(table, Table)
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PG_UUID):
                column.type = Uuid(native_uuid=False)
            if isinstance(column.type, JSONB):
                column.type = JSON()
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            yield session
    finally:
        await engine.dispose()


async def user_and_post(db, *, role=UserRole.USER):
    identity = uuid.uuid4().hex
    user = User(
        email=f"{identity}@example.test",
        username=identity,
        hashed_password="hashed",
        status=AccountStatus.ACTIVE,
        role=role,
    )
    db.add(user)
    await db.flush()
    post = Post(
        user_id=user.id,
        status=ContentStatus.PUBLISHED,
        visibility="public",
        moderation_status="approved",
    )
    db.add(post)
    await db.flush()
    return user, post


@pytest.mark.parametrize("status", list(PaidCampaignStatus))
def test_all_statuses_have_deterministic_eligibility(status):
    assert campaign_is_active(campaign(status=status), NOW) is (status == PaidCampaignStatus.ACTIVE)


@pytest.mark.parametrize(
    "changes",
    [
        {"start_at": NOW + timedelta(seconds=1)},
        {"end_at": NOW},
        {"end_at": NOW - timedelta(seconds=1)},
        {"spent_minor_units": 10_000},
        {"impressions_delivered": 100},
        {"reach_delivered": 50, "impressions_delivered": 50},
    ],
)
def test_campaign_gates_reject_future_expired_and_exhausted(changes):
    assert not campaign_is_active(campaign(**changes), NOW)


def test_start_is_inclusive_end_exclusive_and_expiry_is_not_exhaustion():
    assert campaign_is_active(campaign(start_at=NOW), NOW)
    assert not campaign_is_active(campaign(end_at=NOW), NOW)
    assert campaign_is_expired(campaign(end_at=NOW), NOW)
    assert not campaign_is_exhausted(campaign(end_at=NOW))
    assert campaign_is_exhausted(campaign(max_reach=1, reach_delivered=1))
    assert campaign_is_active(campaign(max_impressions=None, max_reach=None), NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_minor_units": 0},
        {"budget_minor_units": -1},
        {"budget_minor_units": True},
        {"budget_minor_units": 2_147_483_648},
        {"currency": "usd"},
        {"currency": "123"},
        {"end_at": CONFIG.start_at},
        {"start_at": NOW.replace(tzinfo=None)},
        {"max_impressions": 0},
        {"max_reach": 0},
        {"frequency_cap": 1},
        {"frequency_cap": 1, "frequency_window_seconds": 0},
        {"targeting": []},
        {"targeting": {"value": float("nan")}},
        {"targeting": {"text": "x" * 16_384}},
    ],
)
def test_invalid_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(CONFIG, **changes).validated()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.USER, UserRole.CREATOR])
async def test_creation_and_retrieval_persist_owner_relationship_defaults_and_config(db, role):
    owner, post = await user_and_post(db, role=role)
    service = PaidCampaignService(db)
    config = replace(
        CONFIG, targeting={"tags": ["music"]}, frequency_cap=3, frequency_window_seconds=3600
    )
    result = await service.create_campaign(owner, post.id, config)
    await db.commit()
    loaded = await service.get_campaign(owner, result.id)
    assert loaded.id.version == 7
    assert loaded.owner_id == owner.id and loaded.post_id == post.id
    assert loaded.status == PaidCampaignStatus.DRAFT
    assert loaded.targeting == {"tags": ["music"]}
    assert loaded.frequency_cap == 3 and loaded.frequency_window_seconds == 3600
    assert loaded.spent_minor_units == loaded.impressions_delivered == loaded.reach_delivered == 0
    assert loaded.created_at is not None and loaded.updated_at is not None


@pytest.mark.asyncio
async def test_another_owner_including_admin_cannot_read_update_or_promote(db):
    owner, post = await user_and_post(db)
    outsider, _ = await user_and_post(db, role=UserRole.ADMIN)
    service = PaidCampaignService(db)
    result = await service.create_campaign(owner, post.id, CONFIG)
    with pytest.raises(ValueError, match="Campaign not found"):
        await service.get_campaign(outsider, result.id)
    with pytest.raises(ValueError, match="Campaign not found"):
        await service.update_campaign(outsider, result.id, status=PaidCampaignStatus.CANCELLED)
    with pytest.raises(PermissionError, match="post author"):
        await service.create_campaign(outsider, post.id, CONFIG)
    assert result.status == PaidCampaignStatus.DRAFT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [AccountStatus.SUSPENDED, AccountStatus.BANNED, AccountStatus.PENDING_VERIFICATION]
)
async def test_inactive_owner_cannot_manage_campaigns(db, status):
    owner, post = await user_and_post(db)
    owner.status = status
    with pytest.raises(PermissionError, match="Account is not active"):
        await PaidCampaignService(db).create_campaign(owner, post.id, CONFIG)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attribute, value",
    [
        ("status", ContentStatus.DRAFT),
        ("moderation_status", "rejected"),
        ("visibility", "private"),
        ("visibility", "followers"),
        ("deleted_at", NOW),
    ],
)
async def test_unavailable_posts_cannot_be_promoted(db, attribute, value):
    owner, post = await user_and_post(db)
    setattr(post, attribute, value)
    await db.flush()
    with pytest.raises(ValueError):
        await PaidCampaignService(db).create_campaign(owner, post.id, CONFIG)


@pytest.mark.asyncio
async def test_schedule_activate_pause_edit_cancel_and_terminal_rejection(db):
    owner, post = await user_and_post(db)
    service = PaidCampaignService(db)
    future = replace(CONFIG, start_at=NOW + timedelta(hours=1))
    result = await service.create_campaign(owner, post.id, future)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.SCHEDULED, now=NOW)
    assert not campaign_is_active(result, NOW)
    with pytest.raises(ValueError, match="cannot activate"):
        await service.update_campaign(owner, result.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    assert result.status == PaidCampaignStatus.SCHEDULED
    await service.update_campaign(
        owner, result.id, status=PaidCampaignStatus.ACTIVE, now=future.start_at
    )
    assert campaign_is_active(result, future.start_at)
    with pytest.raises(ValueError, match="Only draft or paused"):
        await service.update_campaign(owner, result.id, configuration=CONFIG, now=NOW)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.PAUSED, now=NOW)
    await service.update_campaign(owner, result.id, configuration=CONFIG, now=NOW)
    assert not campaign_is_active(result, NOW)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.CANCELLED, now=NOW)
    with pytest.raises(ValueError, match="transition"):
        await service.update_campaign(owner, result.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    loaded_post = await PostRepository(db).get_by_id(post.id)
    assert loaded_post is not None and loaded_post.status == ContentStatus.PUBLISHED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, changes",
    [
        (PaidCampaignStatus.COMPLETED, {"end_at": NOW}),
        (PaidCampaignStatus.EXHAUSTED, {"spent_minor_units": 10_000}),
        (PaidCampaignStatus.EXHAUSTED, {"impressions_delivered": 100}),
        (PaidCampaignStatus.EXHAUSTED, {"impressions_delivered": 50, "reach_delivered": 50}),
    ],
)
async def test_expiry_and_exhaustion_end_campaign_not_post(db, status, changes):
    owner, post = await user_and_post(db)
    service = PaidCampaignService(db)
    result = await service.create_campaign(owner, post.id, CONFIG)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    for name, value in changes.items():
        setattr(result, name, value)
    await db.flush()
    await service.update_campaign(owner, result.id, status=status, now=NOW)
    assert not campaign_is_active(result, NOW)
    loaded_post = await PostRepository(db).get_by_id(post.id)
    assert loaded_post is not None and loaded_post.status == ContentStatus.PUBLISHED


@pytest.mark.asyncio
async def test_invalid_transitions_and_limit_reduction_do_not_mutate_campaign(db):
    owner, post = await user_and_post(db)
    service = PaidCampaignService(db)
    result = await service.create_campaign(owner, post.id, CONFIG)
    with pytest.raises(ValueError, match="transition"):
        await service.update_campaign(
            owner, result.id, status=PaidCampaignStatus.COMPLETED, now=NOW
        )
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    for status in (PaidCampaignStatus.COMPLETED, PaidCampaignStatus.EXHAUSTED):
        with pytest.raises(ValueError):
            await service.update_campaign(owner, result.id, status=status, now=NOW)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.PAUSED, now=NOW)
    result.spent_minor_units = 100
    result.impressions_delivered = result.reach_delivered = 10
    await db.flush()
    for config in (
        replace(CONFIG, budget_minor_units=99),
        replace(CONFIG, max_impressions=9),
        replace(CONFIG, max_reach=9),
        replace(CONFIG, currency="EUR"),
    ):
        with pytest.raises(ValueError):
            await service.update_campaign(owner, result.id, configuration=config, now=NOW)
    assert result.budget_minor_units == CONFIG.budget_minor_units
    assert result.currency == "USD"


@pytest.mark.asyncio
async def test_repository_candidates_enforce_all_campaign_gates_and_public_content(db):
    owner, post = await user_and_post(db)
    viewer, _ = await user_and_post(db)
    service = PaidCampaignService(db)
    eligible = await service.create_campaign(owner, post.id, CONFIG)
    await service.update_campaign(owner, eligible.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    for status in PaidCampaignStatus:
        if status != PaidCampaignStatus.ACTIVE:
            db.add(campaign(owner_id=owner.id, post_id=post.id, status=status))
    for changes in (
        {"start_at": NOW + timedelta(hours=1)},
        {"end_at": NOW},
        {"spent_minor_units": 10_000},
        {"impressions_delivered": 100},
        {"impressions_delivered": 50, "reach_delivered": 50},
    ):
        db.add(campaign(owner_id=owner.id, post_id=post.id, **changes))
    await db.flush()
    candidates = await service.get_eligible_candidates(viewer, now=NOW)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.paid_campaign_id == eligible.id and candidate.post_id == post.id
    assert candidate.is_sponsored and candidate.sponsored_label == "Sponsored"
    assert {field.name for field in fields(candidate)} == {"post_id", "paid_campaign_id"}
    with pytest.raises(AttributeError):
        setattr(candidate, "is_sponsored", False)
    for attribute, value, original in (
        ("moderation_status", "flagged", "approved"),
        ("visibility", "private", "public"),
        ("status", ContentStatus.REMOVED, ContentStatus.PUBLISHED),
        ("deleted_at", NOW, None),
    ):
        setattr(post, attribute, value)
        await db.flush()
        assert await service.get_eligible_candidates(viewer, now=NOW) == []
        setattr(post, attribute, original)
        await db.flush()
    owner.status = AccountStatus.BANNED
    await db.flush()
    assert await service.get_eligible_candidates(viewer, now=NOW) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["block", "reverse-block", "mute", "private-profile"])
async def test_candidates_respect_viewer_safety_and_creator_privacy(db, restriction):
    owner, post = await user_and_post(db)
    viewer, _ = await user_and_post(db)
    service = PaidCampaignService(db)
    result = await service.create_campaign(owner, post.id, CONFIG)
    await service.update_campaign(owner, result.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    if restriction == "block":
        db.add(UserBlock(blocker_id=viewer.id, blocked_id=owner.id))
    elif restriction == "reverse-block":
        db.add(UserBlock(blocker_id=owner.id, blocked_id=viewer.id))
    elif restriction == "mute":
        db.add(UserMute(muter_id=viewer.id, muted_id=owner.id))
    else:
        db.add(Profile(user_id=owner.id, display_name="private", private_account=True))
    await db.flush()
    assert await service.get_eligible_candidates(viewer, now=NOW) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"budget_minor_units": 0},
        {"spent_minor_units": -1},
        {"spent_minor_units": 10_001},
        {"end_at": CONFIG.start_at},
        {"max_impressions": 0},
        {"max_reach": 0},
        {"impressions_delivered": 101},
        {"impressions_delivered": 51, "reach_delivered": 51},
        {"reach_delivered": 1},
        {"frequency_cap": 1},
        {"currency": "usd"},
        {"owner_id": uuid.uuid4()},
        {"post_id": uuid.uuid4()},
    ],
)
async def test_database_constraints_and_foreign_keys_reject_invalid_rows(db, changes):
    owner, post = await user_and_post(db)
    values = dict(owner_id=owner.id, post_id=post.id)
    values.update(changes)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(campaign(**values))
            await db.flush()


@pytest.mark.asyncio
async def test_campaign_deletion_does_not_delete_post(db):
    owner, post = await user_and_post(db)
    result = await PaidCampaignService(db).create_campaign(owner, post.id, CONFIG)
    await db.delete(result)
    await db.flush()
    assert await PostRepository(db).get_by_id(post.id) is post


CREATE = """
mutation Create($input: CreatePaidCampaignInput!) {
  createPaidCampaign(input: $input) {
    id ownerId postId status budgetMinorUnits spentMinorUnits currency
  }
}
"""


def create_variables(post_id):
    return {
        "input": {
            "postId": str(post_id),
            "configuration": {
                "startAt": CONFIG.start_at.isoformat(),
                "endAt": CONFIG.end_at.isoformat(),
                "budgetMinorUnits": CONFIG.budget_minor_units,
                "currency": "USD",
            },
        }
    }


@pytest.mark.asyncio
async def test_graphql_create_read_update_and_owner_only_details(db):
    owner, post = await user_and_post(db)
    outsider, _ = await user_and_post(db)
    result = await schema.execute(
        CREATE, variable_values=create_variables(post.id), context_value=AppContext(db, owner)
    )
    assert not result.errors
    assert result.data is not None
    data = result.data["createPaidCampaign"]
    assert data["status"] == "DRAFT" and data["spentMinorUnits"] == 0
    query = "query($id: UUID!) { paidCampaign(id: $id) { id budgetMinorUnits } }"
    for user in (None, outsider):
        result = await schema.execute(
            query, variable_values={"id": data["id"]}, context_value=AppContext(db, user)
        )
        assert result.errors and result.data is None
    result = await schema.execute(
        query, variable_values={"id": data["id"]}, context_value=AppContext(db, owner)
    )
    assert not result.errors and result.data is not None
    assert result.data["paidCampaign"]["budgetMinorUnits"] == 10_000
    mutation = """
    mutation($id: UUID!) {
      updatePaidCampaign(id: $id, input: {status: CANCELLED}) { status }
    }
    """
    result = await schema.execute(
        mutation, variable_values={"id": data["id"]}, context_value=AppContext(db, outsider)
    )
    assert result.errors
    result = await schema.execute(
        mutation, variable_values={"id": data["id"]}, context_value=AppContext(db, owner)
    )
    assert not result.errors and result.data is not None
    assert result.data["updatePaidCampaign"]["status"] == "CANCELLED"
    public_type = schema._schema.get_type("FeedItemType")
    assert isinstance(public_type, GraphQLObjectType)
    public_fields = public_type.fields
    assert {"isSponsored", "sponsoredLabel", "paidCampaignId"} <= set(public_fields)
    assert not {"budgetMinorUnits", "spentMinorUnits", "ownerId", "targeting"} & set(public_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["ownerId", "status", "spentMinorUnits", "impressionsDelivered"])
async def test_graphql_rejects_spoofed_owner_status_and_delivery_counters(db, field):
    owner, post = await user_and_post(db)
    variables = create_variables(post.id)
    variables["input"][field] = str(uuid.uuid4()) if field == "ownerId" else 1
    result = await schema.execute(
        CREATE, variable_values=variables, context_value=AppContext(db, owner)
    )
    assert result.errors
    assert (await db.scalars(select(PaidCampaign))).all() == []


@pytest.mark.asyncio
async def test_graphql_anonymous_campaign_creation_is_rejected(db):
    _, post = await user_and_post(db)
    result = await schema.execute(
        CREATE, variable_values=create_variables(post.id), context_value=AppContext(db)
    )
    assert result.errors
    assert (await db.scalars(select(PaidCampaign))).all() == []


@pytest.mark.asyncio
async def test_graphql_configuration_replacement_clears_optional_fields(db):
    owner, post = await user_and_post(db)
    service = PaidCampaignService(db)
    created = await service.create_campaign(
        owner, post.id, replace(CONFIG, targeting={"tags": ["music"]})
    )
    query = """
    mutation($id: UUID!, $input: UpdatePaidCampaignInput!) {
      updatePaidCampaign(id: $id, input: $input) {
        budgetMinorUnits maxImpressions maxReach targeting status
      }
    }
    """
    config = create_variables(post.id)["input"]["configuration"]
    config["budgetMinorUnits"] = 20_000
    result = await schema.execute(
        query,
        variable_values={"id": str(created.id), "input": {"configuration": config}},
        context_value=AppContext(db, owner),
    )
    assert not result.errors and result.data is not None
    assert result.data["updatePaidCampaign"] == {
        "budgetMinorUnits": 20_000,
        "maxImpressions": None,
        "maxReach": None,
        "targeting": None,
        "status": "DRAFT",
    }
    result = await schema.execute(
        query,
        variable_values={"id": str(created.id), "input": {}},
        context_value=AppContext(db, owner),
    )
    assert result.errors and "No campaign update" in result.errors[0].message


@pytest.mark.asyncio
async def test_following_feed_never_requests_paid_candidates(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        AsyncMock(return_value=[]),
    )
    paid = AsyncMock(side_effect=AssertionError("Paid must not enter Following"))
    monkeypatch.setattr(PaidCampaignService, "get_eligible_candidates", paid)
    monkeypatch.setattr(PaidCampaignService, "get_ranked_candidates", paid)
    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", paid)
    page = await _feed(AppContext(db, viewer), None, 20, True)
    assert page.items == [] and page.next_cursor is None
    paid.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_deletion_cascades_campaign_without_campaign_owning_post_lifecycle(db):
    owner, post = await user_and_post(db)
    result = await PaidCampaignService(db).create_campaign(owner, post.id, CONFIG)
    campaign_id = result.id
    await db.delete(post)
    await db.flush()
    db.expunge(result)
    assert await PaidCampaignRepository(db).get_owned(campaign_id, owner.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_private_or_deleted_profile_cannot_create_campaign(db, deleted):
    owner, post = await user_and_post(db)
    db.add(
        Profile(
            user_id=owner.id,
            display_name="restricted",
            private_account=not deleted,
            deleted_at=NOW if deleted else None,
        )
    )
    await db.flush()
    with pytest.raises(ValueError, match="profiles cannot promote"):
        await PaidCampaignService(db).create_campaign(owner, post.id, CONFIG)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_candidate_query_rejects_invalid_limits(db, limit):
    with pytest.raises(ValueError, match="limit"):
        await PaidCampaignRepository(db).get_eligible(NOW, limit=limit)


@pytest.mark.asyncio
async def test_paid_feed_loads_candidates_but_never_delivers_or_records_events(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    candidates = AsyncMock(return_value=[])
    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", candidates)
    following = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids", following
    )
    events = AsyncMock()
    signals = AsyncMock()
    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_impressions_bulk", events
    )
    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.record", signals)
    for name in ("_organic_feed", "_viral_feed", "_community_feed", "_for_you_feed"):
        monkeypatch.setattr(
            f"api.graphql.{name}", AsyncMock(side_effect=AssertionError("mixed feed"))
        )
    result = await schema.execute(
        "{ feed(filter: {algorithm: PAID}) { items { id isSponsored } } }",
        context_value=AppContext(db, viewer),
    )
    assert result.errors and result.errors[0].extensions is not None
    assert result.errors[0].extensions["code"] == "NOT_IMPLEMENTED"
    assert "Paid feed delivery is disabled" in result.errors[0].message
    candidates.assert_awaited_once_with(viewer, cursor=None, limit=10)
    events.assert_not_awaited()
    signals.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "algorithm, following, handler_name",
    [
        (FeedAlgorithm.ORGANIC, False, "_organic_feed"),
        (FeedAlgorithm.VIRAL, False, "_viral_feed"),
        (FeedAlgorithm.COMMUNITY, False, "_community_feed"),
        (None, False, "_for_you_feed"),
    ],
)
async def test_normal_algorithms_do_not_consult_paid_candidates(
    db, monkeypatch, algorithm, following, handler_name
):
    viewer, _ = await user_and_post(db)
    handler = AsyncMock(return_value="normal")
    monkeypatch.setattr(f"api.graphql.{handler_name}", handler)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        AsyncMock(return_value=[]),
    )
    paid = AsyncMock(side_effect=AssertionError("Paid must not enter normal algorithms"))
    monkeypatch.setattr(PaidCampaignService, "get_eligible_candidates", paid)
    monkeypatch.setattr(PaidCampaignService, "get_ranked_candidates", paid)
    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", paid)
    assert await _feed(AppContext(db, viewer), None, 20, following, algorithm) == "normal"
    paid.assert_not_awaited()
    item = FeedItemType(id=uuid.uuid4())
    assert item.is_sponsored is False
    assert item.sponsored_label is None and item.paid_campaign_id is None


@pytest.mark.asyncio
async def test_owner_update_query_uses_postgresql_row_lock():
    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = lambda: None
    await PaidCampaignRepository(db).get_owned(uuid.uuid4(), uuid.uuid4(), for_update=True)
    statement = db.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert "paid_campaigns.owner_id" in str(statement)


@pytest.mark.asyncio
async def test_database_candidate_failures_propagate():
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await PaidCampaignRepository(db).get_eligible(NOW)


def test_migration_emits_matching_postgresql_ddl_and_reversible_downgrade():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "207_paid_campaign_foundation.py"
    # The repository's backend/alembic package shadows the installed CLI package
    # after conftest adds backend to sys.path. Validate DDL in a clean interpreter.
    script = """
import importlib.util
import io
import json
import sys
from alembic.migration import MigrationContext
from alembic.operations import Operations
spec = importlib.util.spec_from_file_location("paid_migration", sys.argv[1])
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
assert migration.down_revision == "206"
output = io.StringIO()
context = MigrationContext.configure(
    dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
)
with Operations.context(context):
    migration.upgrade()
upgrade = output.getvalue()
output.truncate(0)
output.seek(0)
with Operations.context(context):
    migration.downgrade()
print(json.dumps({"upgrade": upgrade, "downgrade": output.getvalue()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    ddl = json.loads(result.stdout)
    sql = ddl["upgrade"]
    assert "CREATE TYPE paid_campaign_status" in sql
    assert "CREATE TABLE paid_campaigns" in sql
    table = PaidCampaign.__table__
    assert isinstance(table, Table)
    for column in table.columns:
        assert column.name in sql
    for constraint in table.constraints:
        if constraint.name:
            assert constraint.name in sql
    for index in table.indexes:
        assert index.name in sql
    sql = ddl["downgrade"]
    assert sql.index("DROP TABLE paid_campaigns") < sql.index("DROP TYPE paid_campaign_status")


@pytest.mark.parametrize(
    "targeting, viewer_tags, expected",
    [
        (None, None, True),
        ({}, None, True),
        (None, ["unrecognized"], True),
        ({"tags": ["music"]}, ["Music"], True),
        ({"tags": ["music"]}, ["travel"], False),
        ({"tags": ["music", "gaming"]}, ["Gaming"], True),
        ({"tags": ["music", "gaming"]}, ["travel", "art"], False),
        ({"tags": [" Music "]}, [" MUSIC "], True),
        ({"tags": ["music"]}, None, False),
        ({"tags": ["music"]}, [], False),
        ({"tags": ["music"]}, "music", False),
        ({"tags": ["music"]}, [None, {}, 1, "music"], True),
    ],
)
def test_targeting_matches_explicit_profile_topics_with_or_semantics(
    targeting, viewer_tags, expected
):
    original = json.dumps([targeting, viewer_tags], sort_keys=True)
    assert targeting_matches(targeting, viewer_tags) is expected
    assert targeting_matches(targeting, viewer_tags) is expected
    assert json.dumps([targeting, viewer_tags], sort_keys=True) == original


@pytest.mark.parametrize("tag", sorted(PAID_TARGET_TAGS))
def test_only_existing_onboarding_topics_are_approved(tag):
    assert validate_targeting({"tags": [tag.upper()]}) == {"tags": [tag]}
    assert targeting_matches({"tags": [tag]}, [tag.upper()])


def test_targeting_storage_is_canonical_sorted_and_input_is_not_modified():
    config = {"tags": ["Tech", " Music ", "ART"]}
    expected = {"tags": ["art", "music", "tech"]}
    assert validate_targeting(config) == expected
    assert replace(CONFIG, targeting=config).validated().targeting == expected
    assert config == {"tags": ["Tech", " Music ", "ART"]}


@pytest.mark.parametrize(
    "targeting",
    [
        [],
        "music",
        1,
        True,
        {"tags": None},
        {"tags": []},
        {"tags": "music"},
        {"tags": [None]},
        {"tags": [1]},
        {"tags": [True]},
        {"tags": [["music"]]},
        {"tags": [""]},
        {"tags": ["   "]},
        {"tags": ["music", " MUSIC "]},
        {"tags": ["unknown-topic"]},
        {"tags": ["religion"]},
        {"tags": ["diabetes"]},
        {"tags": [" " * 65 + "music"]},
        {"tags": ["music"] * 10},
        {"categories": ["music"]},
        {"interests": ["music"]},
        {"tags": ["music"], "race": ["anything"]},
        {"religion": ["anything"]},
        {"health_conditions": ["anything"]},
        {"sexual_orientation": ["anything"]},
        {"ethnicity": ["anything"]},
        {"age": [18]},
        {"location": ["anywhere"]},
    ],
)
def test_invalid_unknown_unsupported_and_sensitive_targeting_is_rejected(targeting):
    with pytest.raises(ValueError):
        validate_targeting(targeting)
    with pytest.raises(ValueError):
        replace(CONFIG, targeting=targeting).validated()
    with pytest.raises(ValueError):
        targeting_matches(targeting, ["music"])


@pytest.mark.asyncio
async def test_create_and_update_validate_targeting_before_mutation(db):
    owner, post = await user_and_post(db)
    service = PaidCampaignService(db)
    with pytest.raises(ValueError, match="topic"):
        await service.create_campaign(
            owner, post.id, replace(CONFIG, targeting={"tags": ["unknown"]})
        )
    assert (await db.scalars(select(PaidCampaign))).all() == []
    created = await service.create_campaign(
        owner, post.id, replace(CONFIG, targeting={"tags": ["Travel", "Music"]})
    )
    assert created.targeting == {"tags": ["music", "travel"]}
    with pytest.raises(ValueError, match="Unsupported"):
        await service.update_campaign(
            owner,
            created.id,
            configuration=replace(CONFIG, targeting={"race": ["anything"]}),
            now=NOW,
        )
    assert created.targeting == {"tags": ["music", "travel"]}
    await service.update_campaign(
        owner, created.id, configuration=replace(CONFIG, targeting={}), now=NOW
    )
    assert created.targeting == {}


@pytest.mark.asyncio
async def test_activation_and_candidate_matching_reject_unvalidated_legacy_targeting(db):
    owner, post = await user_and_post(db)
    service = PaidCampaignService(db)
    created = await service.create_campaign(owner, post.id, CONFIG)
    created.targeting = {"unsupported_legacy_rule": True}
    await db.flush()
    with pytest.raises(ValueError, match="Unsupported"):
        await service.update_campaign(owner, created.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    assert created.status == PaidCampaignStatus.DRAFT
    created.status = PaidCampaignStatus.ACTIVE
    await db.flush()
    with pytest.raises(ValueError, match="Unsupported"):
        await service.get_eligible_candidates(owner, now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "targeting", [{"tags": ["unknown"]}, {"religion": ["anything"]}, {"tags": []}]
)
async def test_graphql_targeting_validation_rejects_invalid_create_and_update(db, targeting):
    owner, post = await user_and_post(db)
    ctx = AppContext(db, owner)
    variables = create_variables(post.id)
    variables["input"]["configuration"]["targeting"] = targeting
    result = await schema.execute(CREATE, variable_values=variables, context_value=ctx)
    assert result.errors and result.errors[0].extensions is not None
    assert result.errors[0].extensions["code"] == "VALIDATION_ERROR"
    assert (await db.scalars(select(PaidCampaign))).all() == []
    variables["input"]["configuration"]["targeting"] = {"tags": ["Music"]}
    result = await schema.execute(CREATE, variable_values=variables, context_value=ctx)
    assert not result.errors and result.data is not None
    campaign_id = result.data["createPaidCampaign"]["id"]
    config = variables["input"]["configuration"]
    config["targeting"] = targeting
    result = await schema.execute(
        """mutation($id: UUID!, $input: UpdatePaidCampaignInput!) {
          updatePaidCampaign(id: $id, input: $input) { targeting }
        }""",
        variable_values={"id": campaign_id, "input": {"configuration": config}},
        context_value=ctx,
    )
    assert result.errors
    loaded = await PaidCampaignService(db).get_campaign(owner, uuid.UUID(campaign_id))
    assert loaded.targeting == {"tags": ["music"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count, status",
    [
        (0, FrequencyStatus.ALLOWED),
        (2, FrequencyStatus.ALLOWED),
        (3, FrequencyStatus.CAPPED),
        (4, FrequencyStatus.CAPPED),
    ],
)
async def test_frequency_counts_only_validated_viewer_campaign_deliveries_in_window(count, status):
    history = AsyncMock()
    history.count_paid_impressions.return_value = count
    subject = campaign(frequency_cap=3, frequency_window_seconds=3600)
    viewer_id = uuid.uuid4()
    result = await PaidFrequencyService(history).check(subject, viewer_id, NOW)
    assert result.status == status
    assert result.impressions == count
    assert result.allowed is (count < 3)
    history.count_paid_impressions.assert_awaited_once_with(
        viewer_id=viewer_id,
        campaign_id=subject.id,
        start_at=NOW - timedelta(hours=1),
        end_at=NOW,
    )
    assert (
        subject.impressions_delivered == subject.spent_minor_units == subject.reach_delivered == 0
    )


@pytest.mark.asyncio
async def test_frequency_no_cap_does_not_require_or_query_impression_history():
    history = AsyncMock()
    history.count_paid_impressions.side_effect = AssertionError("No history needed")
    subject = campaign(frequency_cap=None, frequency_window_seconds=None)
    result = await PaidFrequencyService(history).check(subject, uuid.uuid4(), NOW)
    assert result.allowed and result.impressions is None
    history.count_paid_impressions.assert_not_awaited()


@pytest.mark.asyncio
async def test_frequency_window_uses_one_utc_instant_and_rejects_naive_evaluation_time():
    history = AsyncMock()
    history.count_paid_impressions.return_value = 0
    subject = campaign(frequency_cap=1, frequency_window_seconds=3600)
    service = PaidFrequencyService(history)
    viewer_id = uuid.uuid4()
    moment = NOW.astimezone(timezone(timedelta(hours=-4)))
    assert (await service.check(subject, viewer_id, moment)).allowed
    kwargs = history.count_paid_impressions.await_args.kwargs
    assert kwargs["start_at"] == NOW - timedelta(hours=1)
    assert kwargs["end_at"] == NOW
    assert kwargs["start_at"].tzinfo == kwargs["end_at"].tzinfo == timezone.utc
    history.count_paid_impressions.reset_mock()
    with pytest.raises(ValueError, match="timezone"):
        await service.check(subject, viewer_id, NOW.replace(tzinfo=None))
    history.count_paid_impressions.assert_not_awaited()


@pytest.mark.asyncio
async def test_frequency_unavailable_attribution_is_explicit_and_never_zero_history(monkeypatch):
    db = AsyncMock()
    history = AsyncMock()
    history.count_paid_impressions.side_effect = PaidImpressionHistoryUnavailable(
        "Paid delivery schema is unavailable"
    )
    subject = campaign(frequency_cap=1, frequency_window_seconds=3600)
    warning = Mock()
    monkeypatch.setattr("services.paid_frequency.logger.warning", warning)
    result = await PaidFrequencyService(history).check(subject, uuid.uuid4(), NOW)
    assert result.status == FrequencyStatus.ATTRIBUTION_UNAVAILABLE
    assert result.impressions is None and not result.allowed
    assert result.reason == "Paid delivery schema is unavailable"
    warning.assert_called_once()
    db.execute.assert_not_awaited()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [-1, None, True, 1.5, "0"])
async def test_invalid_frequency_history_count_is_not_false_eligibility(count):
    history = AsyncMock()
    history.count_paid_impressions.return_value = count
    with pytest.raises(ValueError, match="nonnegative integer"):
        await PaidFrequencyService(history).check(
            campaign(frequency_cap=1, frequency_window_seconds=60), uuid.uuid4(), NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cap, window", [(None, 60), (1, None), (0, 60), (1, 0), (True, 60)])
async def test_invalid_persisted_frequency_configuration_is_rejected(cap, window):
    history = AsyncMock()
    with pytest.raises(ValueError, match="specified together"):
        await PaidFrequencyService(history).check(
            campaign(frequency_cap=cap, frequency_window_seconds=window), uuid.uuid4(), NOW
        )
    history.count_paid_impressions.assert_not_awaited()


@pytest.mark.asyncio
async def test_frequency_database_errors_propagate():
    history = AsyncMock()
    history.count_paid_impressions.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await PaidFrequencyService(history).check(
            campaign(frequency_cap=1, frequency_window_seconds=60), uuid.uuid4(), NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes, viewer_tags, count, eligible, frequency_status",
    [
        ({}, ["Music"], 0, True, FrequencyStatus.ALLOWED),
        ({}, ["Music"], 1, False, FrequencyStatus.CAPPED),
        ({}, ["Travel"], 0, False, FrequencyStatus.NOT_CHECKED),
        ({"status": PaidCampaignStatus.PAUSED}, ["Music"], 0, False, FrequencyStatus.NOT_CHECKED),
        ({"end_at": NOW}, ["Music"], 0, False, FrequencyStatus.NOT_CHECKED),
        ({"spent_minor_units": 10_000}, ["Music"], 0, False, FrequencyStatus.NOT_CHECKED),
        ({"impressions_delivered": 100}, ["Music"], 0, False, FrequencyStatus.NOT_CHECKED),
        (
            {"impressions_delivered": 50, "reach_delivered": 50},
            ["Music"],
            0,
            False,
            FrequencyStatus.NOT_CHECKED,
        ),
    ],
)
async def test_combined_eligibility_requires_every_gate(
    db, changes, viewer_tags, count, eligible, frequency_status
):
    viewer, _ = await user_and_post(db)
    db.add(Profile(user_id=viewer.id, display_name="viewer", tags=viewer_tags))
    await db.flush()
    history = AsyncMock()
    history.count_paid_impressions.return_value = count
    subject = campaign(
        targeting={"tags": ["music"]}, frequency_cap=1, frequency_window_seconds=60, **changes
    )
    result = await PaidCampaignService(db, impression_history=history).evaluate_eligibility(
        subject, viewer, now=NOW
    )
    assert result.eligible is eligible
    assert result.frequency.status == frequency_status
    if frequency_status == FrequencyStatus.NOT_CHECKED:
        history.count_paid_impressions.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidates_reuse_profile_tags_and_apply_frequency_without_writes(db):
    owner, post = await user_and_post(db)
    viewer, _ = await user_and_post(db)
    db.add(Profile(user_id=viewer.id, display_name="viewer", tags=["Music"]))
    history = AsyncMock()
    history.count_paid_impressions.return_value = 0
    service = PaidCampaignService(db, impression_history=history)
    matched = await service.create_campaign(
        owner,
        post.id,
        replace(
            CONFIG, targeting={"tags": ["music"]}, frequency_cap=1, frequency_window_seconds=60
        ),
    )
    missed = await service.create_campaign(
        owner, post.id, replace(CONFIG, targeting={"tags": ["travel"]})
    )
    broad = await service.create_campaign(owner, post.id, CONFIG)
    for subject in (matched, missed, broad):
        await service.update_campaign(owner, subject.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    await db.commit()
    before = [
        (
            subject.status,
            subject.updated_at,
            subject.spent_minor_units,
            subject.impressions_delivered,
            subject.reach_delivered,
        )
        for subject in (matched, missed, broad)
    ]
    assert {c.paid_campaign_id for c in await service.get_eligible_candidates(viewer, now=NOW)} == {
        matched.id,
        broad.id,
    }
    history.count_paid_impressions.return_value = 1
    assert [c.paid_campaign_id for c in await service.get_eligible_candidates(viewer, now=NOW)] == [
        broad.id
    ]
    history.count_paid_impressions.side_effect = PaidImpressionHistoryUnavailable(
        "Unavailable attribution"
    )
    assert [c.paid_campaign_id for c in await service.get_eligible_candidates(viewer, now=NOW)] == [
        broad.id
    ]
    assert not db.dirty and not db.new
    assert [
        (
            subject.status,
            subject.updated_at,
            subject.spent_minor_units,
            subject.impressions_delivered,
            subject.reach_delivered,
        )
        for subject in (matched, missed, broad)
    ] == before
    assert (await db.scalars(select(AnalyticsEvent))).all() == []


@pytest.mark.asyncio
async def test_unattributed_or_forged_product_events_cannot_supply_paid_frequency_history(db):
    owner, post = await user_and_post(db)
    viewer, _ = await user_and_post(db)
    service = PaidCampaignService(db)
    subject = await service.create_campaign(
        owner, post.id, replace(CONFIG, frequency_cap=3, frequency_window_seconds=60)
    )
    await service.update_campaign(owner, subject.id, status=PaidCampaignStatus.ACTIVE, now=NOW)
    for event_type, metadata in (
        (EventType.VIDEO_IMPRESSION, None),
        (EventType.VIDEO_VIEWED, None),
        (EventType.LIKE_CREATED, None),
        (EventType.VIDEO_IMPRESSION, {"source": "PAID", "paid_campaign_id": str(subject.id)}),
    ):
        db.add(
            AnalyticsEvent(
                user_id=viewer.id,
                post_id=post.id,
                event_type=event_type,
                event_metadata=metadata,
                created_at=NOW - timedelta(seconds=1),
            )
        )
    await db.commit()
    result = await service.evaluate_eligibility(subject, viewer, now=NOW)
    assert result.eligible and result.frequency.status == FrequencyStatus.ALLOWED
    assert result.frequency.impressions == 0
    assert len(await service.get_eligible_candidates(viewer, now=NOW)) == 1
    assert len((await db.scalars(select(AnalyticsEvent))).all()) == 4


@pytest.mark.asyncio
async def test_atomic_accounting_explicitly_requires_postgres_not_sqlite(db):
    viewer, _ = await user_and_post(db)
    await db.commit()
    with pytest.raises(NotImplementedError, match="requires PostgreSQL"):
        await PaidDeliveryService(db).record_impression(viewer, uuid.uuid4())
    assert (await db.scalars(select(PaidDelivery))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_state", ["missing", "empty", "deleted"])
async def test_restricted_targeting_needs_profile_topics_broad_targeting_does_not(
    db, profile_state
):
    viewer, _ = await user_and_post(db)
    if profile_state != "missing":
        db.add(
            Profile(
                user_id=viewer.id,
                display_name="viewer",
                tags=[] if profile_state == "empty" else ["Music"],
                deleted_at=NOW if profile_state == "deleted" else None,
            )
        )
        await db.flush()
    service = PaidCampaignService(db)
    restricted = await service.evaluate_eligibility(
        campaign(targeting={"tags": ["music"]}), viewer, now=NOW
    )
    assert not restricted.eligible and not restricted.targeting_matches
    assert (await service.evaluate_eligibility(campaign(), viewer, now=NOW)).eligible
