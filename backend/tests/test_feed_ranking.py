"""Pure unit tests for the deterministic For You ranking module.

``repositories/feed_ranking.py`` is DB-free by design, so the exact 100%
weighted score, normalization, penalties, affinity cap, freshness decay, and
diversity rules are tested here directly; resolver-level integration lives in
``test_following_feed.py``.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from repositories import feed_ranking
from repositories.feed_ranking import (
    AFFINITY_NORMALIZER,
    AFFINITY_WEIGHT,
    COMPLETED_PENALTY,
    COMPLETION_WEIGHT,
    ENGAGED_PENALTY,
    FRESHNESS_WEIGHT,
    FRESHNESS_WINDOW_DAYS,
    LIKES_WEIGHT,
    MAX_CONSECUTIVE_PER_CREATOR,
    NOT_INTERESTED_PENALTY,
    REWATCH_WEIGHT,
    SAVES_WEIGHT,
    SEEN_PENALTY,
    SHARES_WEIGHT,
    SHORT_WATCH_PENALTY,
    WATCH_QUALITY_WEIGHT,
    PostEngagementSignals,
    ViewerPostSignals,
    build_engagement,
    diversify_by_creator,
    score_post,
)

NOW = datetime.now(timezone.utc)


def make_ranking_post(
    *,
    age_hours: float = 0.0,
    view_count: int = 0,
    like_count: int = 0,
    share_count: int = 0,
    save_count: int = 0,
    duration_sec: float = 60.0,
    user_id: uuid.UUID | None = None,
    post_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=post_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        created_at=NOW - timedelta(hours=age_hours),
        view_count=view_count,
        like_count=like_count,
        share_count=share_count,
        save_count=save_count,
        duration_sec=duration_sec,
    )


def zero_score_kwargs(post) -> dict:
    """Baseline kwargs: no affinity, no viewer history, no engagement."""
    return dict(
        post=post,
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=None,
        engagement=PostEngagementSignals(),
    )


# ── Exact 100% weighting ─────────────────────────────────────────────────────


def test_weights_sum_to_exactly_one():
    total = (
        WATCH_QUALITY_WEIGHT
        + COMPLETION_WEIGHT
        + REWATCH_WEIGHT
        + SHARES_WEIGHT
        + SAVES_WEIGHT
        + LIKES_WEIGHT
        + AFFINITY_WEIGHT
        + FRESHNESS_WEIGHT
    )
    assert total == pytest.approx(1.0)


def test_score_inputs_are_exactly_the_documented_set():
    """Regression guard: the score must remain a function of content quality,
    behavior, relevance, and freshness only — never popularity multipliers or
    demographic/protected attributes."""
    params = set(inspect.signature(score_post).parameters)
    assert params == {
        "post",
        "now",
        "is_followed",
        "creator_affinity",
        "viewer_history",
        "engagement",
    }
    # Exact-name membership (not substring): no protected characteristic or
    # popularity-multiplier input may ever be added to the score signature.
    banned = {
        "race", "ethnicity", "religion", "gender", "sex", "sexual_orientation",
        "disability", "age", "demographics", "follower_count", "verified",
    }
    assert params.isdisjoint(banned)


def test_maxed_watch_completion_rewatch_affinity_freshness_scores_exactly_75():
    """With watch quality, completion, rewatch, affinity, and freshness at
    their normalized maximum (and zero share/save/like rates), the score must
    be exactly 100 * (0.30 + 0.20 + 0.15 + 0.05 + 0.05) = 75."""
    post = make_ranking_post()
    score = score_post(
        **{
            **zero_score_kwargs(post),
            "creator_affinity": AFFINITY_NORMALIZER,
            "engagement": PostEngagementSignals(
                watch_quality=1.0, completion_rate=1.0, rewatch_rate=1.0
            ),
        }
    )
    assert score == pytest.approx(75.0)


def test_is_followed_has_no_effect_on_score():
    """Follow status is candidate-sourcing metadata, not a score multiplier —
    the core score is exactly the documented 100% formula."""
    post = make_ranking_post()
    followed = score_post(**{**zero_score_kwargs(post), "is_followed": True})
    unfollowed = score_post(**zero_score_kwargs(post))
    assert followed == unfollowed


# ── Normalization & bounds ───────────────────────────────────────────────────


def test_score_is_bounded_even_with_astronomical_raw_counts():
    """Raw view/like totals can never dominate: with every normalized
    component at its ceiling the score approaches but stays under 100."""
    post = make_ranking_post(
        view_count=10**12, like_count=10**12, share_count=10**12, save_count=10**12
    )
    score = score_post(
        **{
            **zero_score_kwargs(post),
            "creator_affinity": 10 * AFFINITY_NORMALIZER,
            "engagement": PostEngagementSignals(1.0, 1.0, 1.0),
        }
    )
    assert 99.0 < score < 100.0


def test_popularity_component_is_capped():
    """Likes+shares+saves together can contribute at most 25 of 100 points,
    no matter how large the raw counts get."""
    huge = make_ranking_post(
        view_count=10**9, like_count=10**9, share_count=10**9, save_count=10**9
    )
    zero = make_ranking_post()
    delta = score_post(**zero_score_kwargs(huge)) - score_post(**zero_score_kwargs(zero))
    assert 0 < delta < SHARES_WEIGHT * 100 + SAVES_WEIGHT * 100 + LIKES_WEIGHT * 100


def test_zero_and_missing_data_never_errors_and_stays_bounded():
    """New content / new creators / no history: zero views, zero counters,
    missing engagement, missing created_at — all must score safely."""
    post = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        created_at=None,  # treated as fresh
        view_count=0,
        like_count=0,
        share_count=0,
        save_count=0,
        duration_sec=0.0,
    )
    score = score_post(
        post=post,
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=None,
        engagement=None,
    )
    # Only freshness contributes (created_at None => treated as brand new).
    assert score == pytest.approx(FRESHNESS_WEIGHT * 100)


def test_score_is_deterministic_for_identical_inputs():
    post = make_ranking_post(view_count=100, like_count=10, share_count=5, save_count=3)
    kwargs = {
        **zero_score_kwargs(post),
        "creator_affinity": 12.5,
        "engagement": PostEngagementSignals(0.4, 0.3, 0.1),
    }
    assert score_post(**kwargs) == score_post(**kwargs)


# ── Creator affinity ─────────────────────────────────────────────────────────


def test_creator_affinity_is_capped_so_one_creator_cannot_dominate():
    post = make_ranking_post()
    at_cap = score_post(**{**zero_score_kwargs(post), "creator_affinity": AFFINITY_NORMALIZER})
    beyond_cap = score_post(
        **{**zero_score_kwargs(post), "creator_affinity": 100 * AFFINITY_NORMALIZER}
    )
    assert at_cap == beyond_cap


def test_creator_affinity_scales_linearly_up_to_the_cap():
    post = make_ranking_post()
    half = score_post(
        **{**zero_score_kwargs(post), "creator_affinity": AFFINITY_NORMALIZER / 2}
    )
    # fresh zero post: freshness (5.0) + half the affinity component (2.5)
    assert half == pytest.approx(FRESHNESS_WEIGHT * 100 + AFFINITY_WEIGHT * 100 / 2)


# ── Freshness ────────────────────────────────────────────────────────────────


def test_freshness_decays_to_zero_over_the_window():
    fresh = make_ranking_post(age_hours=0)
    half = make_ranking_post(age_hours=FRESHNESS_WINDOW_DAYS * 12)
    stale = make_ranking_post(age_hours=FRESHNESS_WINDOW_DAYS * 48)
    assert score_post(**zero_score_kwargs(fresh)) == pytest.approx(5.0)
    assert score_post(**zero_score_kwargs(half)) == pytest.approx(2.5)
    assert score_post(**zero_score_kwargs(stale)) == pytest.approx(0.0)


# ── build_engagement normalization ───────────────────────────────────────────


def test_build_engagement_handles_missing_and_zero_data():
    post = make_ranking_post(duration_sec=60.0)
    assert build_engagement({}, post) == PostEngagementSignals(0.0, 0.0, 0.0)
    # Zero views with watch data present: rates stay 0, no division errors.
    assert build_engagement(
        {"views": 0, "watch_seconds": 100.0, "completions": 5, "rewatches": 2}, post
    ) == PostEngagementSignals(0.0, 0.0, 0.0)


def test_build_engagement_computes_normalized_rates():
    post = make_ranking_post(duration_sec=60.0)
    eng = build_engagement(
        {"views": 100, "watch_seconds": 4500.0, "completions": 150, "rewatches": 75},
        post,
    )
    # avg watch = 4500/100 = 45s of 60s => 0.75
    assert eng.watch_quality == pytest.approx(0.75)
    # additive smoothing (denominator views + RATE_SMOOTHING=100)
    assert eng.completion_rate == pytest.approx(150 / 200)
    assert eng.rewatch_rate == pytest.approx(75 / 200)


def test_build_engagement_clamps_out_of_range_values():
    post = make_ranking_post(duration_sec=60.0)
    eng = build_engagement({"views": 10, "watch_seconds": 10**9}, post)
    assert 0.0 <= eng.watch_quality <= 1.0


# ── Viewer-history penalties & negative signals ──────────────────────────────


def test_penalties_subtract_exactly_their_configured_amounts():
    base = score_post(**zero_score_kwargs(make_ranking_post()))

    seen = score_post(
        post=make_ranking_post(duration_sec=40.0),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(watched_seconds=20.0),  # 50% watched
        engagement=PostEngagementSignals(),
    )
    assert base - seen == pytest.approx(SEEN_PENALTY)

    skipped = score_post(
        post=make_ranking_post(duration_sec=40.0),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(watched_seconds=5.0),  # 12.5% < 25% threshold
        engagement=PostEngagementSignals(),
    )
    assert base - skipped == pytest.approx(SHORT_WATCH_PENALTY)

    completed = score_post(
        post=make_ranking_post(duration_sec=40.0),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(watched_seconds=5.0, completed=True),
        engagement=PostEngagementSignals(),
    )
    # Completion REPLACES the seen/skip penalty (never stacks with them).
    assert base - completed == pytest.approx(COMPLETED_PENALTY)

    engaged = score_post(
        post=make_ranking_post(),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(engaged=True),
        engagement=PostEngagementSignals(),
    )
    assert base - engaged == pytest.approx(ENGAGED_PENALTY)


def test_short_watch_requires_known_duration():
    """With an unknown duration (0), a short absolute watch can't be judged a
    rapid skip — fall back to the plain seen penalty rather than guessing."""
    base = score_post(**zero_score_kwargs(make_ranking_post()))
    seen = score_post(
        post=make_ranking_post(duration_sec=0.0),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(watched_seconds=1.0),
        engagement=PostEngagementSignals(),
    )
    assert base - seen == pytest.approx(SEEN_PENALTY)


def test_not_interested_demotes_post_below_all_other_penalties():
    """Explicit 'Not Interested' is the strongest single-post demotion — below
    completed/seen/skipped/engaged — but still a demotion, not an exclusion."""
    base = score_post(**zero_score_kwargs(make_ranking_post()))

    flagged = score_post(
        post=make_ranking_post(),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(not_interested=True),
        engagement=PostEngagementSignals(),
    )
    assert base - flagged == pytest.approx(NOT_INTERESTED_PENALTY)

    completed = score_post(
        post=make_ranking_post(duration_sec=40.0),
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(watched_seconds=40.0, completed=True),
        engagement=PostEngagementSignals(),
    )
    assert NOT_INTERESTED_PENALTY > COMPLETED_PENALTY
    assert flagged < completed


def test_not_interested_does_not_punish_creator_globally():
    """Flagging one post must not reduce the score of the creator's other
    posts — negative feedback is per-post, never a creator-wide strike."""
    creator = uuid.uuid4()
    flagged_post = make_ranking_post(user_id=creator)
    other_post = make_ranking_post(user_id=creator)

    flagged = score_post(
        post=flagged_post,
        now=NOW,
        is_followed=False,
        creator_affinity=0.0,
        viewer_history=ViewerPostSignals(not_interested=True),
        engagement=PostEngagementSignals(),
    )
    other = score_post(**zero_score_kwargs(other_post))
    baseline_other = score_post(**zero_score_kwargs(other_post))
    assert other == baseline_other
    assert flagged < other


# ── Diversity ────────────────────────────────────────────────────────────────


def test_diversify_caps_consecutive_posts_per_creator_without_dropping_any():
    dominant = uuid.uuid4()
    others = [uuid.uuid4(), uuid.uuid4()]
    # Enough alternatives exist that the streak cap can always be honored.
    scored = [(make_ranking_post(user_id=dominant), 100.0 - i) for i in range(4)]
    scored += [
        (make_ranking_post(user_id=others[0]), 50.0),
        (make_ranking_post(user_id=others[1]), 49.0),
    ]

    ranked = diversify_by_creator(scored)

    assert len(ranked) == len(scored)
    assert {p.id for p in ranked} == {p.id for p, _ in scored}
    streak = 1
    for prev, curr in zip(ranked, ranked[1:]):
        streak = streak + 1 if curr.user_id == prev.user_id else 1
        assert streak <= MAX_CONSECUTIVE_PER_CREATOR


def test_diversify_still_returns_everything_when_only_one_creator_remains():
    """The cap is 'when sufficient alternatives exist': with a single creator
    left, posts are still returned (reordered, never dropped)."""
    solo = uuid.uuid4()
    scored = [(make_ranking_post(user_id=solo), 10.0 - i) for i in range(4)]

    ranked = diversify_by_creator(scored)

    assert len(ranked) == 4
    assert {p.id for p in ranked} == {p.id for p, _ in scored}


def test_diversify_is_deterministic_and_interleaves_creators():
    a, b = uuid.uuid4(), uuid.uuid4()
    scored = [
        (make_ranking_post(user_id=a), 10.0),
        (make_ranking_post(user_id=a), 9.0),
        (make_ranking_post(user_id=a), 8.0),
        (make_ranking_post(user_id=b), 7.0),
        (make_ranking_post(user_id=b), 6.0),
    ]
    first = diversify_by_creator(scored)
    second = diversify_by_creator(list(scored))
    assert [p.id for p in first] == [p.id for p in second]
    # The lower-scoring creator's top post is pulled ahead of the dominant
    # creator's third post once the streak cap is hit.
    creators = [p.user_id for p in first]
    assert creators[:3] == [a, a, b]
