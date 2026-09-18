from dataclasses import dataclass


# Creator Matching v1 weights
INTEREST_WEIGHT = 0.30
BEHAVIORAL_WEIGHT = 0.25
COLLABORATION_WEIGHT = 0.15
FOLLOW_WEIGHT = 0.10
ACTIVITY_WEIGHT = 0.10
REPUTATION_WEIGHT = 0.10


@dataclass(frozen=True)
class CreatorMatchScores:
    interest: float = 0.0
    behavioral: float = 0.0
    collaboration: float = 0.0
    follow: float = 0.0
    activity: float = 0.0
    reputation: float = 0.0


def _clamp_score(value: float) -> float:
    """Keep an individual category score between 0 and 100."""
    return max(0.0, min(100.0, float(value)))


def score_creator(scores: CreatorMatchScores) -> float:
    """
    Calculate the overall Creator Matching v1 score.

    Each category is expected to be scored from 0 to 100.
    The weighted result is also 0 to 100.
    """
    interest = _clamp_score(scores.interest)
    behavioral = _clamp_score(scores.behavioral)
    collaboration = _clamp_score(scores.collaboration)
    follow = _clamp_score(scores.follow)
    activity = _clamp_score(scores.activity)
    reputation = _clamp_score(scores.reputation)

    return (
        interest * INTEREST_WEIGHT
        + behavioral * BEHAVIORAL_WEIGHT
        + collaboration * COLLABORATION_WEIGHT
        + follow * FOLLOW_WEIGHT
        + activity * ACTIVITY_WEIGHT
        + reputation * REPUTATION_WEIGHT
    )

def calculate_interest_score(shared_interests: int) -> float:
    """
    Convert the number of shared interests into a 0-100 score.

    v1:
    0  -> 0
    1  -> 20
    2  -> 40
    3  -> 60
    4  -> 75
    5  -> 85
    6+ -> 90
    """
    if shared_interests <= 0:
        return 0.0
    if shared_interests == 1:
        return 20.0
    if shared_interests == 2:
        return 40.0
    if shared_interests == 3:
        return 60.0
    if shared_interests == 4:
        return 75.0
    if shared_interests == 5:
        return 85.0

    return 90.0

def calculate_behavioral_score(
    views: int = 0,
    meaningful_watches: int = 0,
    completions: int = 0,
    rewatches: int = 0,
    likes: int = 0,
    saves: int = 0,
    shares: int = 0,
    follows: int = 0,
) -> float:
    """
    Calculate a normalized 0-100 behavioral affinity score.

    v1 signal weights:
    View = 1
    Meaningful watch = 2
    Completion = 3
    Rewatch = 4
    Like = 4
    Save = 5
    Share = 6
    Follow = 7
    """

    raw_score = (
    views * 1
    + meaningful_watches * 2
    + completions * 3
    + rewatches * 4
    + likes * 4
    + saves * 5
    + shares * 6
    + follows * 7
)

    # Keep the category score within the agreed 0-100 range.
    return max(0.0, min(100.0, float(raw_score)))

def normalize_behavioral_affinity(
    affinity: float,
    maximum: float = 100.0,
) -> float:
    """
    Convert an existing creator affinity value into a 0-100 score.

    Values at or above maximum receive 100.
    Negative values receive 0.
    """

    if maximum <= 0:
        return 0.0

    return max(
        0.0,
        min(100.0, (float(affinity) / maximum) * 100.0),
    )

def calculate_behavioral_match(affinity: float | None) -> float:
    """
    Convert an existing creator affinity value into the
    Behavioral Affinity category score.
    """

    if affinity is None:
        return 0.0

    return normalize_behavioral_affinity(affinity)

def calculate_collaboration_score(
    open_to_collab: bool = False,
    status_compatible: bool = False,
    shared_interests: int = 0,
    complementary_interests: int = 0,
    positive_history: bool = False,
) -> float:
    """
    Calculate a 0-100 collaboration relevance score.

    Internal v1 weighting:
    Open to collaboration        = 20%
    Status compatibility         = 20%
    Shared interests             = 20%
    Complementary interests      = 25%
    Positive collaboration history = 15%

    Positive collaboration history is currently unavailable in the
    discovery data and should remain False until reliable pairwise data exists.
    """

    score = 0.0

    if open_to_collab:
        score += 20.0

    if status_compatible:
        score += 20.0

    # Use the same interest curve established for Interest Match,
    # but scale the result to this category's 20-point contribution.
    shared_interest_score = calculate_interest_score(shared_interests)
    score += shared_interest_score * 0.20

    # For now, treat complementary interests as a count-based signal.
    # 4+ complementary interests reaches the full 25 points.
    complementary_score = min(complementary_interests, 4) / 4 * 25.0
    score += complementary_score

    if positive_history:
        score += 15.0

    return max(0.0, min(100.0, float(score)))

def calculate_follow_score(
    viewer_follows_creator: bool = False,
    creator_follows_viewer: bool = False,
) -> float:
    """
    Calculate a 0-100 follow relationship score.

    v1:
    No relationship = 50
    One-way relationship = 75
    Mutual follow = 100
    """

    if viewer_follows_creator and creator_follows_viewer:
        return 100.0

    if viewer_follows_creator or creator_follows_viewer:
        return 75.0

    return 50.0

def calculate_activity_score(
    days_since_last_post: float | None = None,
    recent_posts: int = 0,
) -> float:
    """
    Calculate a 0-100 creator activity score.

    v1 considers both post recency and recent posting activity.
    This is intentionally capped so activity cannot dominate matching.
    """

    if days_since_last_post is None and recent_posts <= 0:
        return 20.0

    # Recency component
    if days_since_last_post is None:
        recency_score = 20.0
    elif days_since_last_post <= 1:
        recency_score = 100.0
    elif days_since_last_post <= 3:
        recency_score = 80.0
    elif days_since_last_post <= 7:
        recency_score = 60.0
    elif days_since_last_post <= 14:
        recency_score = 40.0
    else:
        recency_score = 20.0

    # Recent posting component.
    posting_score = min(recent_posts, 5) / 5 * 100.0

    # Balance recency and consistency.
    return max(
        0.0,
        min(100.0, (recency_score * 0.70) + (posting_score * 0.30)),
    )

def calculate_reputation_score(
    successful_collaborations: int = 0,
    positive_outcomes: int = 0,
    engagement_quality: float = 0.0,
    consistency: float = 0.0,
    trust_safety: float = 0.0,
) -> float:
    """
    Calculate a 0-100 reputation score.

    Internal v1 weighting:
    Successful collaborations = 25%
    Positive outcomes         = 25%
    Engagement quality       = 20%
    Consistency               = 15%
    Trust/safety              = 15%

    engagement_quality, consistency, and trust_safety are expected
    to already be normalized from 0-100.
    """

    collaboration_score = min(successful_collaborations, 4) / 4 * 100.0
    outcome_score = min(positive_outcomes, 4) / 4 * 100.0

    engagement_score = max(0.0, min(100.0, engagement_quality))
    consistency_score = max(0.0, min(100.0, consistency))
    trust_score = max(0.0, min(100.0, trust_safety))

    return max(
        0.0,
        min(
            100.0,
            (collaboration_score * 0.25)
            + (outcome_score * 0.25)
            + (engagement_score * 0.20)
            + (consistency_score * 0.15)
            + (trust_score * 0.15),
        ),
    )

def calculate_creator_match(
    interest: float,
    behavioral: float,
    collaboration: float,
    follow: float,
    activity: float,
    reputation: float,
) -> float:
    """
    Calculate the complete Creator Matching v1 score.

    All category values must be on a 0-100 scale.
    """

    scores = CreatorMatchScores(
        interest=interest,
        behavioral=behavioral,
        collaboration=collaboration,
        follow=follow,
        activity=activity,
        reputation=reputation,
    )

    return score_creator(scores)

def count_shared_interests(
    viewer_interests: list[str] | None,
    creator_tags: list[str] | None,
) -> int:
    """
    Count normalized interests shared by the viewer and creator.

    Matching is case-insensitive and ignores surrounding whitespace.
    Duplicate tags are counted only once.
    """

    viewer_set = {
        tag.strip().lower()
        for tag in (viewer_interests or [])
        if isinstance(tag, str) and tag.strip()
    }

    creator_set = {
        tag.strip().lower()
        for tag in (creator_tags or [])
        if isinstance(tag, str) and tag.strip()
    }

    return len(viewer_set & creator_set)

def calculate_interest_match(
    viewer_interests: list[str] | None,
    creator_tags: list[str] | None,
) -> float:
    """
    Calculate the Interest Match category score from 0-100.
    """

    shared_count = count_shared_interests(
        viewer_interests,
        creator_tags,
    )

    return calculate_interest_score(shared_count)

