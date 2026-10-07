"""Deterministic scoring/diversity for the personalized "For You" feed.

Pure functions only (no DB access) so the ranking behavior can be unit
tested and tuned independently of the ``_for_you_feed`` resolver. The core
score is exactly 100%: 30% watch quality/duration, 20% completion rate,
15% rewatch rate, 10% shares, 10% saves, 5% likes, 5% creator affinity,
5% freshness — with every component normalized to [0, 1] before weighting.

Inputs are values already available from existing denormalized post
counters, the follow graph, ``AnalyticsRepository.creator_affinity`` (which
itself aggregates likes/saves/shares/watch-time/completion/rewatch/follow
signals from the unified ``InteractionSignal`` log),
``AnalyticsRepository.post_engagement_rates`` (aggregate watch/completion/
rewatch rates from the same production signal log), and the viewer's own
per-post watch/engagement history (``AnalyticsRepository.viewer_post_history``)
— no new signal sources, no seeded/prototype analytics.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# ── Candidate pool sizing (bounded so scoring stays O(pool), no pagination
# of the underlying query beyond these caps) ────────────────────────────────
FOR_YOU_PERSONAL_POOL_SIZE = 100
FOR_YOU_DISCOVERY_POOL_SIZE = 150
FOR_YOU_DISCOVERY_LOOKBACK_DAYS = 14
FOR_YOU_AFFINITY_POOL_SIZE = 40
FOR_YOU_INTEREST_POOL_SIZE = 60
VIRAL_MOMENTUM_WINDOW_HOURS = 24
COMMUNITY_FOLLOW_POOL_SIZE = 100
COMMUNITY_AFFINITY_POOL_SIZE = 40

# ── Diversity ────────────────────────────────────────────────────────────────
MAX_CONSECUTIVE_PER_CREATOR = 2

# ── Core recommendation score: EXACTLY 100%, split as required ──────────────
# The weighted sum of these constants is exactly 1.00 (100%). No raw view/follower/
# like totals enter the score directly — every input is normalized to [0, 1]
# first, so popularity/history alone cannot dominate ranking.
WATCH_QUALITY_WEIGHT = 0.30   # watch quality / watch duration
COMPLETION_WEIGHT = 0.20      # completion rate
REWATCH_WEIGHT = 0.15         # rewatch rate
SHARES_WEIGHT = 0.10          # shares
SAVES_WEIGHT = 0.10           # saves
LIKES_WEIGHT = 0.05           # likes
AFFINITY_WEIGHT = 0.05        # creator affinity (capped, real interaction history)
FRESHNESS_WEIGHT = 0.05       # freshness (time decay)

# ── Normalization helpers (bounded, deterministic; robust to zero/missing) ──
RATE_SMOOTHING = 100.0   # additive smoothing so small/zero denominators don't blow up
AFFINITY_NORMALIZER = 100.0  # caps creator affinity to [0, 1] (a creator can't dominate)

# ── Freshness ────────────────────────────────────────────────────────────────
FRESHNESS_WINDOW_DAYS = 30.0

# ── Viewer-history penalties (already-consumed content is demoted, never
# hard-excluded, so small/cold-start pools still have something to show) ────
SEEN_PENALTY = 15.0        # any partial view/watch of the post
COMPLETED_PENALTY = 40.0   # watched to completion (replaces SEEN_PENALTY)
ENGAGED_PENALTY = 12.0     # viewer already liked/saved/shared the post

# ── Negative feedback: rapid skip / very short watch ────────────────────────
# A very short watch (relative to the post's duration) is a real production
# signal (WATCH_DURATION with a small value that caused no completion). It
# demotes THAT post only — never the whole creator — so a single short watch
# is not treated as proof the user dislikes an entire creator.
SHORT_WATCH_THRESHOLD = 0.25  # fraction of post duration
SHORT_WATCH_PENALTY = 25.0    # demotes a rapid-skipped post more than a long partial watch

# ── Explicit negative feedback ("Not Interested") ────────────────────────────
# A real production signal (SignalType.NOT_INTERESTED, emitted by the
# not_interested mutation). It demotes THAT post strongly — below any
# already-consumed post — but never the whole creator, and never hard-excludes
# the post (demote, not censor), so cold-start pools never go empty.
NOT_INTERESTED_PENALTY = 60.0

# Recent-event weights for the separate Viral strategy. Shares are the
# clearest available spread signal; lifetime counters do not enter this score.
VIRAL_VIEW_WEIGHT = 1.0
VIRAL_COMPLETION_WEIGHT = 2.0
VIRAL_REWATCH_WEIGHT = 2.0
VIRAL_LIKE_WEIGHT = 2.0
VIRAL_SAVE_WEIGHT = 3.0
VIRAL_SHARE_WEIGHT = 5.0

# Community scores prioritize explicit relationships over interaction affinity.
COMMUNITY_FOLLOW_SCORE = 50.0
COMMUNITY_MUTUAL_FOLLOW_SCORE = 50.0
COMMUNITY_AFFINITY_CAP = 100.0
COMMUNITY_AFFINITY_SCORE_MAX = 25.0


@dataclass(frozen=True)
class ViewerPostSignals:
    """The viewer's own past behavior on a single post, distilled from the
    unified interaction-signal log (``AnalyticsRepository.viewer_post_history``).

    ``watched_seconds`` is the viewer's single best watch event (repeat
    ``post_watches`` rows are collapsed upstream — one post contributes at
    most its best watch to ranking). ``completed`` means any completion
    signal; ``engaged`` means a net like/save or any share; ``not_interested``
    means the viewer explicitly marked the post "Not Interested".
    """

    watched_seconds: float = 0.0
    completed: bool = False
    engaged: bool = False
    not_interested: bool = False


@dataclass(frozen=True)
class PostEngagementSignals:
    """Aggregate production engagement rates for a post, normalized to [0, 1].

    Computed from the unified ``InteractionSignal`` log (real user behavior,
    never seeded/prototype data) by ``AnalyticsRepository.post_engagement_rates``
    combined with the post's own duration. All three fields are bounded rates:
    watch_quality (avg fraction of the post consumed), completion_rate
    (completions / views), rewatch_rate (rewatches / views). Absent data stays 0
    so new content never errors and never fabricates engagement.
    """

    watch_quality: float = 0.0
    completion_rate: float = 0.0
    rewatch_rate: float = 0.0
    views: int | None = None
    likes: int | None = None
    shares: int | None = None
    saves: int | None = None


def _normalized_rate(count: float, denominator: float) -> float:
    """Bounded [0, 1] rate with additive smoothing for zero/small denominators."""
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, count / (denominator + RATE_SMOOTHING)))


def build_engagement(raw: dict, post: Any) -> PostEngagementSignals:
    """Turn ``post_engagement_rates`` raw counts into normalized rates.

    ``raw`` is a dict with optional keys views / watch_seconds / completions /
    rewatches. Watch quality divides total watched seconds by (views *
    duration_sec); completion/rewatch rates divide by views. Any missing value
    (new content, new creators, zero history) yields 0.0 without error.
    """
    views = max(int(raw.get("views", 0) or 0), 0)
    duration_sec = max(getattr(post, "duration_sec", 0) or 0, 0.0)
    watch_seconds = max(float(raw.get("watch_seconds", 0.0) or 0.0), 0.0)
    completions = max(int(raw.get("completions", 0) or 0), 0)
    rewatches = max(int(raw.get("rewatches", 0) or 0), 0)

    if views > 0 and duration_sec > 0:
        watch_quality = max(0.0, min(1.0, (watch_seconds / views) / duration_sec))
    else:
        watch_quality = 0.0

    return PostEngagementSignals(
        watch_quality=watch_quality,
        completion_rate=_normalized_rate(completions, views),
        rewatch_rate=_normalized_rate(rewatches, views),
        views=(
            max(int(raw["views_total"] or 0), 0)
            if "views_total" in raw
            else None
        ),
        likes=max(int(raw["likes"] or 0), 0) if "likes" in raw else None,
        shares=max(int(raw["shares"] or 0), 0) if "shares" in raw else None,
        saves=max(int(raw["saves"] or 0), 0) if "saves" in raw else None,
    )


def score_viral_post(recent_signals: dict[str, float]) -> float:
    """Score recent momentum using only timestamp-windowed interaction events."""
    net_likes = max(
        float(recent_signals.get("likes", 0.0) or 0.0)
        - float(recent_signals.get("unlikes", 0.0) or 0.0),
        0.0,
    )
    net_saves = max(
        float(recent_signals.get("saves", 0.0) or 0.0)
        - float(recent_signals.get("unsaves", 0.0) or 0.0),
        0.0,
    )
    return (
        max(float(recent_signals.get("views", 0.0) or 0.0), 0.0) * VIRAL_VIEW_WEIGHT
        + max(float(recent_signals.get("completions", 0.0) or 0.0), 0.0)
        * VIRAL_COMPLETION_WEIGHT
        + max(float(recent_signals.get("rewatches", 0.0) or 0.0), 0.0)
        * VIRAL_REWATCH_WEIGHT
        + net_likes * VIRAL_LIKE_WEIGHT
        + net_saves * VIRAL_SAVE_WEIGHT
        + max(float(recent_signals.get("shares", 0.0) or 0.0), 0.0)
        * VIRAL_SHARE_WEIGHT
    )


def score_community_post(
    *, is_followed: bool, is_mutual: bool, creator_affinity: float
) -> float:
    """Rank by current follow relationships, reciprocity, and viewer affinity."""
    followed_score = COMMUNITY_FOLLOW_SCORE if is_followed else 0.0
    mutual_score = COMMUNITY_MUTUAL_FOLLOW_SCORE if is_mutual else 0.0
    affinity = max(0.0, min(creator_affinity, COMMUNITY_AFFINITY_CAP))
    affinity_score = affinity / COMMUNITY_AFFINITY_CAP * COMMUNITY_AFFINITY_SCORE_MAX
    return followed_score + mutual_score + affinity_score


def score_post(
    *,
    post: Any,
    now: datetime,
    is_followed: bool,
    creator_affinity: float,
    viewer_history: ViewerPostSignals | None = None,
    engagement: PostEngagementSignals | None = None,
) -> float:
    """Deterministic relevance score for a single candidate post.

    The core score is EXACTLY the required 100% weighted combination:
      30% watch quality/duration, 20% completion rate, 15% rewatch rate,
      10% shares, 10% saves, 5% likes, 5% creator affinity, 5% freshness.
    Every component is normalized to [0, 1] before the weights are applied, so
    raw view/follower/like totals or historical popularity can never dominate
    ranking. Deterministic for identical inputs; bounded above by 100 plus any
    viewer-history penalty (penalties only demote already-consumed posts).
    """
    default_engagement = PostEngagementSignals()
    engagement = engagement or default_engagement

    views = max(
        engagement.views
        if engagement.views is not None
        else (getattr(post, "view_count", 0) or 0),
        0,
    )
    likes = max(
        engagement.likes
        if engagement.likes is not None
        else (getattr(post, "like_count", 0) or 0),
        0,
    )
    shares = max(
        engagement.shares
        if engagement.shares is not None
        else (getattr(post, "share_count", 0) or 0),
        0,
    )
    saves = max(
        engagement.saves
        if engagement.saves is not None
        else (getattr(post, "save_count", 0) or 0),
        0,
    )

    # Normalized [0, 1] components (bounded; missing values => 0).
    watch_quality = max(0.0, min(1.0, engagement.watch_quality))
    completion_rate = max(0.0, min(1.0, engagement.completion_rate))
    rewatch_rate = max(0.0, min(1.0, engagement.rewatch_rate))
    shares_rate = _normalized_rate(shares, views)
    saves_rate = _normalized_rate(saves, views)
    likes_rate = _normalized_rate(likes, views)
    affinity = max(0.0, min(1.0, max(creator_affinity, 0.0) / AFFINITY_NORMALIZER))

    created_at = getattr(post, "created_at", None) or now
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_hours = max((now - created_at).total_seconds() / 3600.0, 0.0)
    freshness = max(0.0, min(1.0, 1.0 - age_hours / (FRESHNESS_WINDOW_DAYS * 24.0)))

    core = 100.0 * (
        WATCH_QUALITY_WEIGHT * watch_quality
        + COMPLETION_WEIGHT * completion_rate
        + REWATCH_WEIGHT * rewatch_rate
        + SHARES_WEIGHT * shares_rate
        + SAVES_WEIGHT * saves_rate
        + LIKES_WEIGHT * likes_rate
        + AFFINITY_WEIGHT * affinity
        + FRESHNESS_WEIGHT * freshness
    )

    history_penalty = 0.0
    if viewer_history is not None:
        if viewer_history.completed:
            history_penalty += COMPLETED_PENALTY
        elif viewer_history.watched_seconds > 0:
            duration_sec = max(getattr(post, "duration_sec", 0) or 0, 0.0)
            if duration_sec > 0 and viewer_history.watched_seconds / duration_sec < SHORT_WATCH_THRESHOLD:
                history_penalty += SHORT_WATCH_PENALTY  # rapid skip / very short watch
            else:
                history_penalty += SEEN_PENALTY
        if viewer_history.engaged:
            history_penalty += ENGAGED_PENALTY
        if viewer_history.not_interested:
            history_penalty += NOT_INTERESTED_PENALTY  # explicit negative feedback

    return core - history_penalty


def diversify_by_creator(
    scored: list[tuple[Any, float]],
    max_consecutive: int = MAX_CONSECUTIVE_PER_CREATOR,
    view_counts: dict[uuid.UUID, int] | None = None,
) -> list[Any]:
    """Reorder score-sorted candidates so no creator dominates a run.

    Greedily takes the highest-scoring available post at each step, unless
    doing so would extend the same creator's streak past ``max_consecutive``,
    in which case the next-best post from a different creator is taken
    instead (falling back to the same creator if no other candidates
    remain). Never drops a candidate, only reorders. Deterministic: ties are
    broken by post id so output is stable given identical inputs.
    """
    order = sorted(
        scored,
        # Deterministic: (score, then raw view count purely as an equal-score
        # tie-break, then post id). Views are NOT part of the score itself, so
        # popularity can never dominate — it only stabilizes exact ties.
        key=lambda item: (
            item[1],
            max(
                (view_counts or {}).get(
                    item[0].id, getattr(item[0], "view_count", 0) or 0
                ),
                0,
            ),
            str(item[0].id),
        ),
        reverse=True,
    )

    buckets: dict[uuid.UUID, deque[tuple[Any, float]]] = defaultdict(deque)
    for post, score in order:
        buckets[post.user_id].append((post, score))

    result: list[Any] = []
    last_creator: uuid.UUID | None = None
    streak = 0
    while buckets:
        ordered_creators = sorted(
            buckets.keys(), key=lambda uid: buckets[uid][0][1], reverse=True
        )
        chosen = next(
            (
                uid
                for uid in ordered_creators
                if not (uid == last_creator and streak >= max_consecutive)
            ),
            ordered_creators[0],
        )
        post, _score = buckets[chosen].popleft()
        if not buckets[chosen]:
            del buckets[chosen]
        streak = streak + 1 if chosen == last_creator else 1
        last_creator = chosen
        result.append(post)
    return result
