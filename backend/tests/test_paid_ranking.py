"""Paid-only ranking over the existing eligible pipeline; no delivery side effects."""

from __future__ import annotations

import uuid
from datetime import timedelta, timezone
from itertools import permutations
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event, select

from app.models.analytics import AnalyticsEvent
from app.models.paid_campaign import PaidCampaignStatus
from app.models.paid_delivery import PaidDelivery
from app.models.social import UserMute
from app.models.user import Profile
from services.paid_campaign_service import PaidCampaignService
from services.paid_ranking import PaidRankingService
from tests.test_paid_campaigns import NOW, campaign
from tests.test_paid_campaigns import db as db
from tests.test_paid_campaigns import user_and_post


def ranked(subjects, now=NOW):
    return PaidRankingService.rank_eligible(subjects, now=now)


def test_ranking_is_deterministic_with_uuid_ties_and_does_not_mutate_input():
    subjects = [campaign(id=uuid.UUID(int=value)) for value in (3, 1, 2)]
    before = [dict(subject.__dict__) for subject in subjects]
    for ordering in permutations(subjects):
        assert [subject.id.int for subject in ranked(list(ordering))] == [1, 2, 3]
    assert [subject.__dict__ for subject in subjects] == before
    assert [subject.id.int for subject in subjects] == [3, 1, 2]


@pytest.mark.parametrize(
    "changes",
    [
        {"impressions_delivered": 80, "max_reach": None},
        {"impressions_delivered": 40, "reach_delivered": 40},
    ],
)
def test_more_remaining_delivery_opportunity_ranks_first(changes):
    constrained = campaign(**changes)
    available = campaign(impressions_delivered=10, reach_delivered=5)
    assert ranked([constrained, available]) == [available, constrained]


def test_pacing_deficit_takes_priority_over_remaining_capacity():
    urgent = campaign(
        start_at=NOW - timedelta(hours=9),
        end_at=NOW + timedelta(hours=1),
        impressions_delivered=70,
        max_reach=None,
    )
    later = campaign(
        start_at=NOW - timedelta(hours=2),
        end_at=NOW + timedelta(hours=8),
        impressions_delivered=10,
        max_reach=None,
    )
    # Deficits are 0.2 and 0.1, despite remaining fractions of 0.3 and 0.9.
    assert ranked([later, urgent]) == [urgent, later]


def test_equal_pacing_deficits_prefer_more_remaining_opportunity():
    subjects = [
        campaign(
            start_at=NOW - timedelta(hours=elapsed),
            end_at=NOW + timedelta(hours=10 - elapsed),
            impressions_delivered=elapsed * 10,
            max_reach=None,
        )
        for elapsed in (8, 2)
    ]
    assert ranked(subjects) == list(reversed(subjects))


def test_pacing_changes_with_schedule_and_uses_authoritative_counter_state():
    fast = campaign(
        start_at=NOW - timedelta(hours=1),
        end_at=NOW + timedelta(hours=1),
        impressions_delivered=40,
        max_reach=None,
    )
    slow = campaign(
        start_at=NOW - timedelta(hours=1),
        end_at=NOW + timedelta(hours=3),
        impressions_delivered=10,
        max_reach=None,
    )
    assert ranked([fast, slow]) == [slow, fast]
    assert ranked([fast, slow], NOW + timedelta(minutes=30)) == [fast, slow]
    fast.impressions_delivered = 80
    assert ranked([fast, slow], NOW + timedelta(minutes=30)) == [slow, fast]
    # Soft pacing deprioritizes, but does not exclude, an ahead-of-pace campaign.
    assert ranked([fast]) == [fast]


def test_uncapped_campaigns_balance_impressions_without_inventing_budget_pricing():
    busy = campaign(max_impressions=None, max_reach=None, impressions_delivered=50)
    quiet = campaign(
        max_impressions=None,
        max_reach=None,
        impressions_delivered=5,
        budget_minor_units=1,
        currency="EUR",
    )
    assert ranked([busy, quiet]) == [quiet, busy]
    quiet.budget_minor_units = 2_000_000
    assert ranked([busy, quiet]) == [quiet, busy]


def test_neutral_uncapped_pacing_is_between_behind_and_ahead_of_pace():
    uncapped = campaign(max_impressions=None, max_reach=None)
    behind = campaign()
    ahead = campaign(impressions_delivered=50, max_reach=None)
    assert ranked([ahead, uncapped, behind]) == [behind, uncapped, ahead]


def test_reach_only_cap_and_timezone_normalization():
    constrained = campaign(max_impressions=None, reach_delivered=40, impressions_delivered=40)
    available = campaign(max_impressions=None, reach_delivered=5, impressions_delivered=5)
    expected = [available, constrained]
    assert ranked([constrained, available]) == expected
    assert (
        ranked([constrained, available], NOW.astimezone(timezone(timedelta(hours=-4)))) == expected
    )
    for subject in expected:
        subject.start_at = subject.start_at.replace(tzinfo=None)
        subject.end_at = subject.end_at.replace(tzinfo=None)
    assert ranked([constrained, available]) == expected


def test_empty_pool_and_invalid_evaluation_time():
    assert ranked([]) == []
    with pytest.raises(ValueError, match="timezone"):
        ranked([], NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="after start"):
        ranked([campaign(start_at=NOW, end_at=NOW)])


@pytest.mark.asyncio
async def test_every_eligibility_gate_precedes_ranking_and_ranking_never_writes(db, monkeypatch):
    owner, post = await user_and_post(db)
    viewer, _ = await user_and_post(db)
    hidden_owner, hidden_post = await user_and_post(db)
    unsafe_owner, unsafe_post = await user_and_post(db)
    unsafe_post.moderation_status = "flagged"
    db.add(Profile(user_id=viewer.id, display_name="viewer", tags=["music"]))
    db.add(UserMute(muter_id=viewer.id, muted_id=hidden_owner.id))
    history = AsyncMock()
    history.count_paid_impressions.return_value = 1
    service = PaidCampaignService(db, impression_history=history)
    available = campaign(owner_id=owner.id, post_id=post.id)
    ahead = campaign(owner_id=owner.id, post_id=post.id, impressions_delivered=80)
    excluded = [
        campaign(owner_id=owner.id, post_id=post.id, **changes)
        for changes in (
            {"status": PaidCampaignStatus.PAUSED},
            {"start_at": NOW + timedelta(seconds=1)},
            {"end_at": NOW},
            {"spent_minor_units": 10_000},
            {"impressions_delivered": 100},
            {"impressions_delivered": 50, "reach_delivered": 50},
            {"targeting": {"tags": ["travel"]}},
            {"frequency_cap": 1, "frequency_window_seconds": 60},
        )
    ] + [
        campaign(owner_id=hidden_owner.id, post_id=hidden_post.id),
        campaign(owner_id=unsafe_owner.id, post_id=unsafe_post.id),
    ]
    subjects = [ahead, available, *excluded]
    db.add_all(subjects)
    await db.commit()
    before = [
        tuple(getattr(subject, column.key) for column in subject.__table__.columns)
        for subject in subjects
    ]
    scorer = Mock(wraps=PaidRankingService.rank_eligible)
    monkeypatch.setattr(PaidRankingService, "rank_eligible", scorer)
    writes = []

    def capture_writes(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_writes)
    try:
        result = await service.get_ranked_candidates(viewer, now=NOW)
    finally:
        event.remove(bind, "before_cursor_execute", capture_writes)
    assert [candidate.paid_campaign_id for candidate in result] == [available.id, ahead.id]
    assert all(
        candidate.is_sponsored and candidate.sponsored_label == "Sponsored" for candidate in result
    )
    scorer.assert_called_once()
    assert {subject.id for subject in scorer.call_args.args[0]} == {available.id, ahead.id}
    assert scorer.call_args.kwargs == {"now": NOW}
    assert not db.new and not db.dirty and not db.deleted
    assert [
        tuple(getattr(subject, column.key) for column in subject.__table__.columns)
        for subject in subjects
    ] == before
    assert writes == []
    assert (await db.scalars(select(PaidDelivery))).all() == []
    assert (await db.scalars(select(AnalyticsEvent))).all() == []
    history.count_paid_impressions.assert_awaited_once()


@pytest.mark.asyncio
async def test_frequency_attribution_failure_stays_excluded_from_ranking(db, monkeypatch):
    from repositories.paid_impression_history_repository import PaidImpressionHistoryUnavailable

    owner, post = await user_and_post(db)
    history = AsyncMock()
    history.count_paid_impressions.side_effect = PaidImpressionHistoryUnavailable("incomplete")
    service = PaidCampaignService(db, impression_history=history)
    db.add(
        campaign(owner_id=owner.id, post_id=post.id, frequency_cap=1, frequency_window_seconds=60)
    )
    await db.commit()
    scorer = Mock(wraps=PaidRankingService.rank_eligible)
    monkeypatch.setattr(PaidRankingService, "rank_eligible", scorer)
    assert await service.get_ranked_candidates(owner, now=NOW) == []
    scorer.assert_called_once_with([], now=NOW)
