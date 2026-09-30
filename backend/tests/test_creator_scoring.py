"""Focused tests for creator matching score calculations."""

import pytest

from repositories.creator_scoring import (
    calculate_collaboration_score,
    calculate_creator_match,
    calculate_interest_score,
    count_shared_interests,
)


@pytest.mark.parametrize(
    ("shared_interests", "expected"),
    [
        (-1, 0.0),
        (0, 0.0),
        (1, 20.0),
        (2, 40.0),
        (3, 60.0),
        (4, 75.0),
        (5, 85.0),
        (6, 90.0),
        (100, 90.0),
    ],
)
def test_calculate_interest_score_uses_v1_curve(shared_interests, expected):
    assert calculate_interest_score(shared_interests) == expected


@pytest.mark.parametrize(
    ("viewer_interests", "creator_tags", "expected"),
    [
        (None, None, 0),
        ([], ["music"], 0),
        ([" Music ", "music", "ART"], ["music", " art ", "music"], 2),
        (["music", "", "  ", None, 42], ["MUSIC", "  ", None, 42], 1),
    ],
)
def test_count_shared_interests_normalizes_deduplicates_and_ignores_invalid_tags(
    viewer_interests,
    creator_tags,
    expected,
):
    assert count_shared_interests(viewer_interests, creator_tags) == expected


def test_calculate_creator_match_applies_weights_and_clamps_categories():
    assert calculate_creator_match(100, 80, 60, 40, 20, 0) == pytest.approx(65.0)
    assert calculate_creator_match(200, -10, 50, 50, 50, 50) == pytest.approx(52.5)


def test_calculate_creator_match_returns_zero_for_zero_categories():
    assert calculate_creator_match(0, 0, 0, 0, 0, 0) == 0.0


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, 0.0),
        ({"open_to_collab": True}, 20.0),
        ({"status_compatible": True}, 20.0),
        ({"shared_interests": 6}, 18.0),
        ({"complementary_interests": 4}, 25.0),
        ({"complementary_interests": 10}, 25.0),
        ({"positive_history": True}, 15.0),
    ],
)
def test_calculate_collaboration_score_applies_each_signal_and_caps_counts(kwargs, expected):
    assert calculate_collaboration_score(**kwargs) == expected


def test_calculate_collaboration_score_combines_signals_and_has_lower_bound():
    assert calculate_collaboration_score(
        open_to_collab=True,
        status_compatible=True,
        shared_interests=4,
        complementary_interests=2,
        positive_history=True,
    ) == pytest.approx(82.5)
    assert calculate_collaboration_score(complementary_interests=-1) == 0.0