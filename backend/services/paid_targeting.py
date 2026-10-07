"""Explicit topic targeting over existing profile tags, without profiling."""

from __future__ import annotations

# These are the existing onboarding topics, not a second category taxonomy.
PAID_TARGET_TAGS = frozenset(
    {"music", "fitness", "travel", "cooking", "art", "tech", "gaming", "fashion", "business"}
)


def validate_targeting(targeting: object) -> dict[str, list[str]] | None:
    if targeting is None:
        return None
    if not isinstance(targeting, dict):
        raise ValueError("Targeting must be a JSON object")
    if set(targeting) - {"tags"}:
        raise ValueError("Unsupported targeting fields; only tags are supported")
    if not targeting:
        return {}
    tags = targeting["tags"]
    if not isinstance(tags, list) or not 1 <= len(tags) <= len(PAID_TARGET_TAGS):
        raise ValueError("Targeting tags must be a nonempty list of approved topics")
    normalized = []
    for tag in tags:
        if not isinstance(tag, str) or len(tag) > 64:
            raise ValueError("Targeting tags must be strings of at most 64 characters")
        value = tag.strip().casefold()
        if value not in PAID_TARGET_TAGS:
            raise ValueError("Unknown or unsupported targeting topic")
        if value in normalized:
            raise ValueError("Duplicate targeting topics are not allowed")
        normalized.append(value)
    return {"tags": sorted(normalized)}


def targeting_matches(targeting: object, viewer_tags: object) -> bool:
    """No restriction matches all; otherwise at least one approved tag must match."""
    config = validate_targeting(targeting)
    if not config:
        return True
    if not isinstance(viewer_tags, list):
        return False
    topics = {
        tag.strip().casefold()
        for tag in viewer_tags
        if isinstance(tag, str) and tag.strip().casefold() in PAID_TARGET_TAGS
    }
    return bool(topics.intersection(config["tags"]))
