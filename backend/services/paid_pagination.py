"""Viewer-bound ranked snapshots, never Paid delivery authorization."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass

from app.config import settings

MAX_PAID_CANDIDATES = 100
PAID_CURSOR_PREFIX = "paid1."
MAX_CURSOR_LENGTH = 16_000


@dataclass(frozen=True)
class PaidCursor:
    identities: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    last_campaign_id: uuid.UUID

    def encode(self, viewer_id: uuid.UUID) -> str:
        identities = ",".join(
            f"{campaign_id}:{post_id}" for campaign_id, post_id in self.identities
        )
        body = f"{PAID_CURSOR_PREFIX}{viewer_id}.{self.last_campaign_id}.{identities}"
        return f"{body}.{_signature(body)}"

    @classmethod
    def decode(cls, cursor: str, viewer_id: uuid.UUID) -> PaidCursor:
        if (
            len(cursor) > MAX_CURSOR_LENGTH
            or not cursor.isascii()
            or not cursor.startswith(PAID_CURSOR_PREFIX)
        ):
            raise ValueError("Invalid Paid feed cursor")
        body, _, signature = cursor.rpartition(".")
        if not hmac.compare_digest(_signature(body), signature):
            raise ValueError("Invalid Paid feed cursor")
        try:
            prefix, owner, anchor, identities = body.split(".")
            pairs = tuple(
                (uuid.UUID(campaign_id), uuid.UUID(post_id))
                for campaign_id, post_id in (pair.split(":") for pair in identities.split(","))
            )
            last_id = uuid.UUID(anchor)
            if (
                prefix != PAID_CURSOR_PREFIX.rstrip(".")
                or uuid.UUID(owner) != viewer_id
                or not 1 <= len(pairs) <= MAX_PAID_CANDIDATES
                or len({campaign_id for campaign_id, _ in pairs}) != len(pairs)
                or len({post_id for _, post_id in pairs}) != len(pairs)
                or last_id not in {campaign_id for campaign_id, _ in pairs}
            ):
                raise ValueError("Invalid Paid feed cursor")
        except ValueError:
            raise ValueError("Invalid Paid feed cursor") from None
        return cls(pairs, last_id)


def _signature(body: str) -> str:
    key = settings.jwt_secret_key.get_secret_value().encode()
    return hmac.new(key, f"paid-pagination:{body}".encode(), hashlib.sha256).hexdigest()
