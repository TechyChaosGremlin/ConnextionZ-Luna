"""Campaign-aware, read-only pagination; the public Paid feed remains disabled."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event, select

from api.graphql import AppContext, _feed, schema
from app.models.analytics import AnalyticsEvent
from app.models.paid_campaign import PaidCampaignStatus
from app.models.paid_delivery import PaidDelivery
from app.models.social import UserBlock, UserMute
from app.models.user import AccountStatus, Profile
from services.paid_campaign_service import PaidCampaignService
from services.paid_delivery_service import PaidDeliveryService
from services.paid_pagination import PaidCursor
from services.paid_ranking import PaidRankingService
from tests.test_paid_campaigns import NOW, campaign
from tests.test_paid_campaigns import db as db
from tests.test_paid_campaigns import user_and_post


async def add_campaign(db, identity, **changes):
    owner, post = await user_and_post(db)
    subject = campaign(id=uuid.UUID(int=identity), owner_id=owner.id, post_id=post.id, **changes)
    db.add(subject)
    return subject, owner, post


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2, 3, 100])
async def test_pages_preserve_full_ranking_uuid_ties_and_paid_identity(db, monkeypatch, limit):
    viewer, _ = await user_and_post(db)
    subjects = [(await add_campaign(db, identity))[0] for identity in (5, 3, 1, 4, 2)]
    await db.commit()
    service = PaidCampaignService(db)
    scorer = Mock(wraps=PaidRankingService.rank_eligible)
    monkeypatch.setattr(PaidRankingService, "rank_eligible", scorer)
    page = await service.get_candidate_page(viewer, now=NOW, limit=limit)
    all_items = list(page.items)
    while page.next_cursor is not None:
        cursor = page.next_cursor
        page = await service.get_candidate_page(viewer, cursor=cursor, now=NOW, limit=limit)
        retry = await service.get_candidate_page(viewer, cursor=cursor, now=NOW, limit=limit)
        assert retry == page
        all_items.extend(page.items)
    assert [item.paid_campaign_id.int for item in all_items] == [1, 2, 3, 4, 5]
    assert len({item.post_id for item in all_items}) == 5
    assert {item.post_id for item in all_items} == {subject.post_id for subject in subjects}
    assert all(item.is_sponsored and item.sponsored_label == "Sponsored" for item in all_items)
    scorer.assert_called_once()
    assert len(scorer.call_args.args[0]) == 5
    assert scorer.call_args.kwargs == {"now": NOW}


@pytest.mark.asyncio
async def test_snapshot_does_not_rerank_as_pacing_time_advances(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    top, _, _ = await add_campaign(
        db,
        1,
        start_at=NOW - timedelta(hours=9),
        end_at=NOW + timedelta(hours=1),
        max_reach=None,
    )
    fast, _, _ = await add_campaign(
        db,
        2,
        start_at=NOW - timedelta(hours=1),
        end_at=NOW + timedelta(hours=1),
        impressions_delivered=40,
        max_reach=None,
    )
    slow, _, _ = await add_campaign(
        db,
        3,
        start_at=NOW - timedelta(hours=1),
        end_at=NOW + timedelta(hours=3),
        impressions_delivered=10,
        max_reach=None,
    )
    await db.commit()
    later = NOW + timedelta(minutes=30)
    assert PaidRankingService.rank_eligible([fast, slow], now=NOW) == [slow, fast]
    assert PaidRankingService.rank_eligible([fast, slow], now=later) == [fast, slow]
    service = PaidCampaignService(db)
    first = await service.get_candidate_page(viewer, now=NOW, limit=1)
    assert first.items[0].paid_campaign_id == top.id
    monkeypatch.setattr(
        PaidRankingService, "rank_eligible", Mock(side_effect=AssertionError("reranked page"))
    )
    second = await service.get_candidate_page(viewer, cursor=first.next_cursor, now=later, limit=2)
    assert [item.paid_campaign_id for item in second.items] == [slow.id, fast.id]
    assert second.next_cursor is None


@pytest.mark.asyncio
async def test_same_promoted_post_is_returned_once_using_highest_ranked_campaign(db):
    viewer, _ = await user_and_post(db)
    first, owner, post = await add_campaign(db, 1)
    db.add(campaign(id=uuid.UUID(int=2), owner_id=owner.id, post_id=post.id))
    last, _, _ = await add_campaign(db, 3)
    await db.commit()
    service = PaidCampaignService(db)
    page = await service.get_candidate_page(viewer, now=NOW, limit=1)
    assert page.items[0].paid_campaign_id == first.id
    page = await service.get_candidate_page(viewer, cursor=page.next_cursor, now=NOW, limit=1)
    assert [item.paid_campaign_id for item in page.items] == [last.id]
    assert page.next_cursor is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "restriction",
    [
        "paused",
        "future",
        "expired",
        "budget",
        "impressions",
        "reach",
        "targeting",
        "frequency",
        "moderation",
        "private-post",
        "deleted-post",
        "banned-owner",
        "private-profile",
        "block",
        "mute",
        "changed-post",
    ],
)
async def test_continuation_revalidates_all_gates_and_skips_ineligible_anchor(db, restriction):
    viewer, _ = await user_and_post(db)
    db.add(Profile(user_id=viewer.id, display_name="viewer", tags=["music"]))
    anchor, _, _ = await add_campaign(db, 1)
    rejected, owner, post = await add_campaign(
        db, 2, targeting={"tags": ["music"]}, frequency_cap=1, frequency_window_seconds=60
    )
    tail, _, _ = await add_campaign(db, 3)
    await db.commit()
    history = AsyncMock()
    history.count_paid_impressions.return_value = 0
    service = PaidCampaignService(db, impression_history=history)
    first = await service.get_candidate_page(viewer, now=NOW, limit=1)
    anchor.status = PaidCampaignStatus.PAUSED
    if restriction == "paused":
        rejected.status = PaidCampaignStatus.PAUSED
    elif restriction == "future":
        rejected.start_at = NOW + timedelta(seconds=1)
    elif restriction == "expired":
        rejected.end_at = NOW
    elif restriction == "budget":
        rejected.spent_minor_units = rejected.budget_minor_units
    elif restriction == "impressions":
        assert rejected.max_impressions is not None
        rejected.impressions_delivered = rejected.max_impressions
    elif restriction == "reach":
        assert rejected.max_reach is not None
        rejected.reach_delivered = rejected.max_reach
        rejected.impressions_delivered = rejected.max_reach
    elif restriction == "targeting":
        rejected.targeting = {"tags": ["travel"]}
    elif restriction == "frequency":
        history.count_paid_impressions.return_value = 1
    elif restriction == "moderation":
        post.moderation_status = "flagged"
    elif restriction == "private-post":
        post.visibility = "private"
    elif restriction == "deleted-post":
        post.deleted_at = NOW
    elif restriction == "banned-owner":
        owner.status = AccountStatus.BANNED
    elif restriction == "private-profile":
        db.add(Profile(user_id=owner.id, display_name="private", private_account=True))
    elif restriction == "block":
        db.add(UserBlock(blocker_id=viewer.id, blocked_id=owner.id))
    elif restriction == "mute":
        db.add(UserMute(muter_id=viewer.id, muted_id=owner.id))
    elif restriction == "changed-post":
        _, replacement = await user_and_post(db)
        replacement.user_id = owner.id
        rejected.post_id = replacement.id
    await db.commit()
    second = await service.get_candidate_page(viewer, cursor=first.next_cursor, now=NOW, limit=1)
    assert [item.paid_campaign_id for item in second.items] == [tail.id]
    assert second.next_cursor is None
    if restriction != "changed-post":
        fresh = await service.get_candidate_page(viewer, now=NOW, limit=100)
        assert [item.paid_campaign_id for item in fresh.items] == [tail.id]


@pytest.mark.asyncio
async def test_empty_exhausted_and_all_remaining_ineligible_pages(db):
    viewer, _ = await user_and_post(db)
    service = PaidCampaignService(db)
    empty = await service.get_candidate_page(viewer, now=NOW)
    assert empty.items == [] and empty.next_cursor is None
    first, _, _ = await add_campaign(db, 1)
    final, _, _ = await add_campaign(db, 2)
    await db.commit()
    page = await service.get_candidate_page(viewer, now=NOW, limit=1)
    assert page.next_cursor is not None
    snapshot = PaidCursor.decode(page.next_cursor, viewer.id)
    exhausted_cursor = PaidCursor(snapshot.identities, final.id).encode(viewer.id)
    exhausted = await service.get_candidate_page(viewer, cursor=exhausted_cursor, now=NOW)
    assert exhausted == empty
    first.status = final.status = PaidCampaignStatus.PAUSED
    await db.commit()
    assert await service.get_candidate_page(viewer, cursor=page.next_cursor, now=NOW) == empty


@pytest.mark.asyncio
async def test_snapshot_candidates_are_not_displaced_by_new_sql_pool_entries(db):
    viewer, _ = await user_and_post(db)
    for identity in range(1, 101):
        await add_campaign(db, identity)
    await db.commit()
    service = PaidCampaignService(db)
    page = await service.get_candidate_page(viewer, now=NOW, limit=99)
    newcomer, _, _ = await add_campaign(db, 101)
    newcomer.created_at = NOW - timedelta(days=1)
    await db.commit()
    tail = await service.get_candidate_page(viewer, cursor=page.next_cursor, now=NOW, limit=99)
    assert [item.paid_campaign_id.int for item in tail.items] == [100]
    assert tail.next_cursor is None


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_invalid_page_limit_is_explicit(db, limit):
    viewer, _ = await user_and_post(db)
    with pytest.raises(ValueError, match="Paid page limit"):
        await PaidCampaignService(db).get_candidate_page(viewer, limit=limit, now=NOW)


@pytest.mark.asyncio
async def test_cursor_rejects_tampering_wrong_viewer_and_non_paid_formats(db):
    viewer, _ = await user_and_post(db)
    stranger, _ = await user_and_post(db)
    for identity in (1, 2, 3):
        await add_campaign(db, identity)
    await db.commit()
    service = PaidCampaignService(db)
    page = await service.get_candidate_page(viewer, now=NOW, limit=1)
    assert page.next_cursor is not None
    invalid = [
        "",
        str(uuid.uuid4()),
        "fy1.not-paid",
        "paid1.broken",
        "paid1." + "x" * 16_000,
        "paid1.\u00e9",
        page.next_cursor[:-1] + ("0" if page.next_cursor[-1] != "0" else "1"),
    ]
    for cursor in invalid:
        with pytest.raises(ValueError, match="Invalid Paid feed cursor"):
            await service.get_candidate_page(viewer, cursor=cursor, now=NOW)
    with pytest.raises(ValueError, match="Invalid Paid feed cursor"):
        await service.get_candidate_page(stranger, cursor=page.next_cursor, now=NOW)


@pytest.mark.parametrize(
    "kind", ["empty", "duplicate-campaign", "duplicate-post", "anchor", "large"]
)
def test_cursor_shape_validation_even_for_signed_payloads(kind):
    viewer = uuid.uuid4()
    identities = ((uuid.UUID(int=1), uuid.UUID(int=2)),)
    anchor = identities[0][0]
    if kind == "empty":
        identities = ()
    elif kind == "duplicate-campaign":
        identities += ((anchor, uuid.UUID(int=3)),)
    elif kind == "duplicate-post":
        identities += ((uuid.UUID(int=3), identities[0][1]),)
    elif kind == "anchor":
        anchor = uuid.UUID(int=4)
    elif kind == "large":
        identities = tuple((uuid.UUID(int=i), uuid.UUID(int=i + 200)) for i in range(1, 102))
    with pytest.raises(ValueError, match="Invalid Paid feed cursor"):
        PaidCursor.decode(PaidCursor(identities, anchor).encode(viewer), viewer)


@pytest.mark.asyncio
async def test_pagination_and_retries_perform_no_accounting_or_sql_writes(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    subjects = [
        (await add_campaign(db, identity, frequency_cap=3, frequency_window_seconds=60))[0]
        for identity in (1, 2, 3)
    ]
    await db.commit()
    before = [
        tuple(getattr(subject, column.key) for column in subject.__table__.columns)
        for subject in subjects
    ]
    history = AsyncMock()
    history.count_paid_impressions.return_value = 0
    service = PaidCampaignService(db, impression_history=history)
    forbidden = AsyncMock(side_effect=AssertionError("pagination cannot account delivery"))
    monkeypatch.setattr(PaidDeliveryService, "select_candidate", forbidden)
    monkeypatch.setattr(PaidDeliveryService, "record_impression", forbidden)
    monkeypatch.setattr(
        "services.analytics_event_service.AnalyticsEventService.track_impressions_bulk", forbidden
    )
    writes = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        first = await service.get_candidate_page(viewer, now=NOW, limit=1)
        assert await service.get_candidate_page(viewer, now=NOW, limit=1) == first
        second = await service.get_candidate_page(
            viewer, cursor=first.next_cursor, now=NOW, limit=2
        )
        assert (
            await service.get_candidate_page(viewer, cursor=first.next_cursor, now=NOW, limit=2)
            == second
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert writes == []
    forbidden.assert_not_awaited()
    assert not db.new and not db.dirty and not db.deleted
    assert before == [
        tuple(getattr(subject, column.key) for column in subject.__table__.columns)
        for subject in subjects
    ]
    assert (await db.scalars(select(PaidDelivery))).all() == []
    assert (await db.scalars(select(AnalyticsEvent))).all() == []
    assert history.count_paid_impressions.await_count == 12


@pytest.mark.asyncio
async def test_paid_cursor_reaches_only_paid_handler_and_public_delivery_stays_disabled(
    db, monkeypatch
):
    viewer, _ = await user_and_post(db)
    for identity in (1, 2):
        await add_campaign(db, identity)
    await db.commit()
    page = await PaidCampaignService(db).get_candidate_page(viewer, now=NOW, limit=1)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        AsyncMock(return_value=[]),
    )
    paginated = AsyncMock(return_value=page)
    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", paginated)
    forbidden = AsyncMock(side_effect=AssertionError("Paid cannot use non-Paid handlers"))
    for name in ("_organic_feed", "_viral_feed", "_community_feed", "_for_you_feed"):
        monkeypatch.setattr(f"api.graphql.{name}", forbidden)
    result = await schema.execute(
        "query($cursor: String!) { feed(cursor: $cursor, filter: {algorithm: PAID}) { nextCursor } }",
        variable_values={"cursor": page.next_cursor},
        context_value=AppContext(db, viewer),
    )
    assert result.errors and result.errors[0].message == "Paid feed delivery is disabled"
    assert result.errors[0].extensions is not None
    assert result.errors[0].extensions["code"] == "NOT_IMPLEMENTED"
    paginated.assert_awaited_once_with(viewer, cursor=page.next_cursor, limit=10)
    forbidden.assert_not_awaited()
    with pytest.raises(ValueError, match="Invalid feed cursor"):
        await _feed(AppContext(db, viewer), page.next_cursor, 10, True)
