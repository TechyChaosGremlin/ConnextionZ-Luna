from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.analytics import EventType, SignalType
from app.models.paid_delivery import PaidInteractionContext
from repositories.analytics_event_repository import AnalyticsEventRepository
from repositories.analytics_repository import AnalyticsRepository
from repositories.feed_ranking import (
    PostEngagementSignals,
    diversify_by_creator,
    score_post,
)
from repositories.paid_delivery_repository import PaidDeliveryRepository
from repositories.social_repository import PostInteractionRepository
from services.analytics_event_service import AnalyticsEventService
from services.paid_interaction_context_service import PaidInteractionContextService


class RecordingSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    def begin_nested(self):
        @asynccontextmanager
        async def nested():
            yield self

        return nested()


class PaidContextSession(RecordingSession):
    def __init__(self, context=None):
        super().__init__()
        self.context = context
        self.now = datetime.now(timezone.utc)
        self.statements = []

    async def scalar(self, _statement):
        return self.now

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(scalar_one_or_none=lambda: self.context)


def paid_receipt(viewer_id, post_id):
    return SimpleNamespace(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        viewer_id=viewer_id,
        post_id=post_id,
        impressed_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_paid_interaction_context_is_server_issued_hashed_and_bound(monkeypatch):
    viewer_id, post_id, delivery_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    selection = SimpleNamespace(
        id=delivery_id,
        viewer_id=viewer_id,
        post_id=post_id,
    )
    db = PaidContextSession()
    monkeypatch.setattr(PaidDeliveryRepository, "require_postgresql", lambda _self: None)
    get_selection = AsyncMock(return_value=selection)
    monkeypatch.setattr(PaidDeliveryRepository, "get_selection", get_selection)

    token = await PaidInteractionContextService(db).issue(viewer_id, delivery_id)

    assert isinstance(token, str) and token
    get_selection.assert_awaited_once_with(delivery_id, viewer_id)
    stored = db.added[0]
    assert isinstance(stored, PaidInteractionContext)
    assert stored.delivery_id == delivery_id
    assert stored.viewer_id == viewer_id
    assert stored.post_id == post_id
    assert stored.token_hash == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert stored.token_hash != token
    assert stored.expires_at == db.now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_paid_interaction_context_is_bound_and_consumed_once(monkeypatch):
    viewer_id, post_id, delivery_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = "one-use-paid-context"
    context = SimpleNamespace(
        delivery_id=delivery_id,
        viewer_id=viewer_id,
        post_id=post_id,
        token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        consumed_at=None,
    )
    db = PaidContextSession(context)
    receipt = paid_receipt(viewer_id, post_id)
    receipt.id = delivery_id
    monkeypatch.setattr(PaidDeliveryRepository, "require_postgresql", lambda _self: None)
    validate_delivery = AsyncMock(return_value=receipt)
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        validate_delivery,
    )
    service = PaidInteractionContextService(db)

    await service.consume(
        token,
        viewer_id=viewer_id,
        post_id=post_id,
        delivery_id=delivery_id,
    )

    assert context.consumed_at == db.now
    validate_delivery.assert_awaited_once_with(delivery_id, viewer_id, post_id)
    statement = db.statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert "paid_interaction_contexts.token_hash =" in sql
    with pytest.raises(ValueError, match="invalid or expired"):
        await service.consume(
            token,
            viewer_id=viewer_id,
            post_id=post_id,
            delivery_id=delivery_id,
        )


@pytest.mark.asyncio
async def test_forged_paid_interaction_context_does_not_establish_attribution(monkeypatch):
    viewer_id, post_id, delivery_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = PaidContextSession()
    monkeypatch.setattr(PaidDeliveryRepository, "require_postgresql", lambda _self: None)
    validate_delivery = AsyncMock(return_value=paid_receipt(viewer_id, post_id))
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        validate_delivery,
    )

    with pytest.raises(ValueError, match="invalid or expired"):
        await PaidInteractionContextService(db).consume(
            "forged-random-context",
            viewer_id=viewer_id,
            post_id=post_id,
            delivery_id=delivery_id,
        )

    validate_delivery.assert_awaited_once_with(delivery_id, viewer_id, post_id)


@pytest.mark.asyncio
async def test_paid_delivery_lookup_is_limited_to_impressed_viewer_post_pair():
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()
    receipt = paid_receipt(viewer_id, post_id)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: receipt)

    resolved = await PaidDeliveryRepository(db).latest_impressed_for_engagement(viewer_id, post_id)

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile())
    assert resolved is receipt
    assert "paid_deliveries.viewer_id =" in sql
    assert "paid_deliveries.post_id =" in sql
    assert "paid_deliveries.impressed_at IS NOT NULL" in sql
    assert "ORDER BY paid_deliveries.impressed_at DESC" in sql


@pytest.mark.asyncio
async def test_invalid_paid_receipt_fails_without_guessing():
    db = AsyncMock()
    invalid = SimpleNamespace(
        id="not-a-uuid",
        campaign_id=uuid.uuid4(),
        viewer_id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        impressed_at=datetime.now(timezone.utc),
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: invalid)

    with pytest.raises(ValueError, match="provenance is invalid"):
        await PaidDeliveryRepository(db).latest_impressed_for_engagement(uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_exact_paid_delivery_reference_is_bound_to_viewer_post_and_impression():
    viewer_id, post_id, delivery_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)

    with pytest.raises(ValueError, match="provenance is invalid"):
        await PaidDeliveryRepository(db).impressed_for_engagement(delivery_id, viewer_id, post_id)

    sql = str(db.execute.await_args.args[0].compile())
    assert "paid_deliveries.id =" in sql
    assert "paid_deliveries.viewer_id =" in sql
    assert "paid_deliveries.post_id =" in sql
    assert "paid_deliveries.impressed_at IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_analytics_event_uses_server_receipt_and_ignores_forged_paid_metadata(
    monkeypatch,
):
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()
    receipt = paid_receipt(viewer_id, post_id)
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        AsyncMock(return_value=receipt),
    )
    db = RecordingSession()
    event = await AnalyticsEventService(db).track_event(
        event_type=EventType.VIDEO_VIEWED,
        user=SimpleNamespace(id=viewer_id),
        post=SimpleNamespace(id=post_id),
        paid_delivery_id=receipt.id,
        metadata={
            "source": "paid",
            "algorithm": "PAID",
            "isSponsored": True,
            "paid_campaign_id": str(uuid.uuid4()),
            "paid_delivery_id": str(uuid.uuid4()),
            "client_note": "kept",
        },
    )

    assert event is not None
    assert event.paid_delivery_id == receipt.id
    assert event.paid_campaign_id == receipt.campaign_id
    assert event.event_metadata == {
        "client_note": "kept",
        "source": "paid",
        "paid_campaign_id": str(receipt.campaign_id),
        "paid_delivery_id": str(receipt.id),
    }


@pytest.mark.asyncio
async def test_missing_receipt_does_not_accept_client_paid_claim(monkeypatch):
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        AsyncMock(return_value=None),
    )
    db = RecordingSession()
    event = await AnalyticsEventService(db).track_event(
        event_type=EventType.LIKE_CREATED,
        user=SimpleNamespace(id=viewer_id),
        post=SimpleNamespace(id=post_id),
        metadata={
            "source": "paid",
            "algorithm": "paid",
            "isSponsored": True,
            "paidCampaignId": str(uuid.uuid4()),
        },
    )

    assert event is not None
    assert event.paid_delivery_id is None
    assert event.paid_campaign_id is None
    assert event.event_metadata is None


@pytest.mark.asyncio
async def test_attribution_lookup_failure_keeps_event_but_marks_provenance_unavailable(
    monkeypatch,
):
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()

    async def fail_lookup(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(PaidDeliveryRepository, "impressed_for_engagement", fail_lookup)
    db = RecordingSession()
    event = await AnalyticsEventService(db).track_event(
        event_type=EventType.VIDEO_VIEWED,
        user=SimpleNamespace(id=viewer_id),
        post=SimpleNamespace(id=post_id),
        metadata={"source": "organic_feed"},
        paid_delivery_id=uuid.uuid4(),
    )

    assert event is None
    assert db.added == []


@pytest.mark.asyncio
async def test_recommendation_signal_receives_authoritative_paid_receipt(monkeypatch):
    viewer_id, post_id, creator_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    receipt = paid_receipt(viewer_id, post_id)
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        AsyncMock(return_value=receipt),
    )
    db = RecordingSession()

    signal = await AnalyticsRepository(db).record(
        user_id=viewer_id,
        creator_id=creator_id,
        post_id=post_id,
        signal_type=SignalType.LIKE,
        paid_delivery_id=receipt.id,
    )

    assert signal.paid_delivery_id == receipt.id
    assert signal.paid_campaign_id == receipt.campaign_id


@pytest.mark.asyncio
async def test_active_post_interaction_uses_receipt_provenance(monkeypatch):
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()
    receipt = paid_receipt(viewer_id, post_id)
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        AsyncMock(return_value=receipt),
    )
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(first=lambda: None),
        SimpleNamespace(rowcount=1),
    ]

    await PostInteractionRepository(db).toggle_like(post_id, viewer_id, paid_delivery_id=receipt.id)

    insert_statement = db.execute.await_args_list[1].args[0]
    params = insert_statement.compile().params
    assert receipt.id in params.values()
    assert receipt.campaign_id in params.values()


@pytest.mark.asyncio
async def test_paid_watch_row_uses_receipt_provenance(monkeypatch):
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()
    receipt = paid_receipt(viewer_id, post_id)
    monkeypatch.setattr(
        PaidDeliveryRepository,
        "impressed_for_engagement",
        AsyncMock(return_value=receipt),
    )
    db = AsyncMock()
    db.add = Mock()
    db.execute.return_value = SimpleNamespace(scalar_one=lambda: 0)

    row = await PostInteractionRepository(db).track_watch(
        post_id,
        viewer_id,
        watched_seconds=8.0,
        completed=False,
        paid_delivery_id=receipt.id,
    )

    assert row.paid_delivery_id == receipt.id
    assert row.paid_campaign_id == receipt.campaign_id


@pytest.mark.asyncio
async def test_recommendation_aggregates_exclude_paid_signals():
    viewer_id, post_id = uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [])
    repository = AnalyticsRepository(db)

    await repository.creator_affinity(viewer_id)
    assert "interaction_signals.paid_delivery_id IS NULL" in str(
        db.execute.await_args.args[0].compile()
    )
    await repository.viewer_post_history(viewer_id, [post_id])
    assert "interaction_signals.paid_delivery_id IS NULL" in str(
        db.execute.await_args.args[0].compile()
    )
    await repository.post_engagement_rates([post_id])
    assert "interaction_signals.paid_delivery_id IS NULL" in str(
        db.execute.await_args.args[0].compile()
    )
    await repository.recent_post_engagement([post_id], datetime.now(timezone.utc))
    assert "interaction_signals.paid_delivery_id IS NULL" in str(
        db.execute.await_args.args[0].compile()
    )
    await repository.user_interest_tags(viewer_id)
    assert "interaction_signals.paid_delivery_id IS NULL" in str(
        db.execute.await_args.args[0].compile()
    )

    await repository.signal_totals(paid_campaign_id=uuid.uuid4())
    assert "interaction_signals.paid_campaign_id =" in str(db.execute.await_args.args[0].compile())


@pytest.mark.asyncio
async def test_recommendation_counter_aggregate_excludes_paid_interactions():
    post_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [])

    counts = await PostInteractionRepository(db).recommendation_engagement_counts([post_id])

    sql = str(db.execute.await_args.args[0].compile())
    assert sql.count("paid_delivery_id IS NULL") == 4
    assert counts[post_id] == {"views": 0, "likes": 0, "saves": 0, "shares": 0}


@pytest.mark.asyncio
async def test_paid_events_remain_queryable_by_campaign():
    campaign_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    assert await AnalyticsEventRepository(db).get_for_paid_campaign(campaign_id) == []

    statement = db.execute.await_args.args[0]
    assert "analytics_events.paid_campaign_id =" in str(statement.compile())


def test_for_you_scoring_uses_non_paid_counters_instead_of_post_totals():
    now = datetime.now(timezone.utc)
    post_with_paid_totals = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        created_at=now,
        duration_sec=30,
        view_count=1000,
        like_count=900,
        save_count=500,
        share_count=200,
    )
    clean_post = SimpleNamespace(
        id=post_with_paid_totals.id,
        user_id=post_with_paid_totals.user_id,
        created_at=now,
        duration_sec=30,
        view_count=0,
        like_count=0,
        save_count=0,
        share_count=0,
    )
    non_paid_counts = PostEngagementSignals(views=0, likes=0, saves=0, shares=0)
    kwargs = {
        "now": now,
        "is_followed": False,
        "creator_affinity": 0.0,
        "engagement": non_paid_counts,
    }

    assert score_post(post=post_with_paid_totals, **kwargs) == score_post(post=clean_post, **kwargs)


def test_diversity_tie_break_uses_non_paid_view_counts_when_provided():
    first_creator, second_creator = uuid.uuid4(), uuid.uuid4()
    first_id, second_id = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
    first = SimpleNamespace(id=first_id, user_id=first_creator, view_count=1000)
    second = SimpleNamespace(id=second_id, user_id=second_creator, view_count=0)

    ranked = diversify_by_creator(
        [(first, 10.0), (second, 10.0)],
        view_counts={first_id: 0, second_id: 0},
    )

    assert [post.id for post in ranked] == [second_id, first_id]
