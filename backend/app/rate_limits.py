"""Process-local sliding-window limits, shared by all requests to a router."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import Callable, Mapping
from uuid import UUID

RATE_LIMIT_MESSAGE = "Too many requests. Please slow down and try again later."


@dataclass(frozen=True)
class ActionLimit:
    requests: int
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.requests < 1 or self.window_seconds < 1:
            raise ValueError("Rate limits and windows must be positive")


AUTH_ENDPOINT_ACTIONS = MappingProxyType(
    {
        "/auth/login": "auth_login",
        "/auth/register": "auth_register",
        "/auth/refresh": "auth_refresh",
        "/auth/logout": "auth_logout",
    }
)

AUTH_ENDPOINT_LIMITS = MappingProxyType(
    {
        "auth_login": ActionLimit(10),
        "auth_register": ActionLimit(5, window_seconds=60 * 60),
        "auth_refresh": ActionLimit(30),
        "auth_logout": ActionLimit(30),
    }
)

UPLOAD_REQUEST_ACTION = "media_upload_request"
UPLOAD_VOLUME_ACTION = "media_upload_volume_bytes"
UPLOAD_REQUEST_LIMITS = MappingProxyType(
    {UPLOAD_REQUEST_ACTION: ActionLimit(10, window_seconds=60 * 60)}
)
UPLOAD_VOLUME_LIMITS = MappingProxyType(
    {UPLOAD_VOLUME_ACTION: ActionLimit(1024 * 1024 * 1024, window_seconds=60 * 60)}
)

STREAM_START_ACTION = "stream_start"
STREAM_START_HOURLY_ACTION = "stream_start_hourly"
STREAM_STOP_ACTION = "stream_stop"
STREAM_VIEWER_JOIN_ACTION = "stream_viewer_join"
STREAM_VIEWER_HEARTBEAT_ACTION = "stream_viewer_heartbeat"
STREAM_VIEWER_LEAVE_ACTION = "stream_viewer_leave"
STREAM_CONCURRENT_RETRY_SECONDS = 60
STREAM_ACTION_LIMITS = MappingProxyType(
    {
        STREAM_START_ACTION: ActionLimit(2),
        STREAM_START_HOURLY_ACTION: ActionLimit(10, window_seconds=60 * 60),
        STREAM_STOP_ACTION: ActionLimit(10),
    }
)

STREAM_VIEWER_ACTION_LIMITS = MappingProxyType(
    {
        STREAM_VIEWER_JOIN_ACTION: ActionLimit(20),
        STREAM_VIEWER_HEARTBEAT_ACTION: ActionLimit(30),
        STREAM_VIEWER_LEAVE_ACTION: ActionLimit(20),
    }
)


class ActionRateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(RATE_LIMIT_MESSAGE)


def client_identity(user_id: UUID | str | None, client_ip: str) -> tuple[str, str]:
    return ("user", str(user_id)) if user_id is not None else ("ip", client_ip)


class ActionRateLimiter:
    """Atomically reserve action costs before executing any side effects."""

    def __init__(
        self,
        limits: Mapping[str, ActionLimit],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not limits:
            raise ValueError("At least one action limit is required")
        self.limits = dict(limits)
        self._clock = clock
        self._history: dict[tuple[tuple[str, str], str], deque[float]] = {}
        self._lock = Lock()
        self._last_cleanup = clock()
        self._cleanup_interval = max(limit.window_seconds for limit in limits.values())

    def consume(self, identity: tuple[str, str], costs: Mapping[str, int]) -> None:
        if any(cost < 1 for cost in costs.values()):
            raise ValueError("Action costs must be positive")
        with self._lock:
            now = self._clock()
            if now - self._last_cleanup >= self._cleanup_interval:
                for key, bucket in list(self._history.items()):
                    window = self.limits[key[1]].window_seconds
                    while bucket and now - bucket[0] >= window:
                        bucket.popleft()
                    if not bucket:
                        del self._history[key]
                self._last_cleanup = now

            retry_after = 0
            for action, cost in costs.items():
                limit = self.limits[action]
                bucket = self._history.get((identity, action), deque())
                while bucket and now - bucket[0] >= limit.window_seconds:
                    bucket.popleft()
                if cost > limit.requests:
                    retry_after = max(retry_after, limit.window_seconds)
                elif len(bucket) + cost > limit.requests:
                    # Enough prior actions must expire to fit the entire request.
                    expiry = bucket[len(bucket) + cost - limit.requests - 1]
                    retry_after = max(retry_after, math.ceil(expiry + limit.window_seconds - now))
            if retry_after:
                raise ActionRateLimitExceeded(max(1, retry_after))

            for action, cost in costs.items():
                bucket = self._history.setdefault((identity, action), deque())
                bucket.extend([now] * cost)
