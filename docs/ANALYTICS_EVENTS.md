# Analytics Event Tracking v1

Centralized, append-only product-analytics event log. This is **foundational
event tracking only** — it does not include a creator/admin analytics
dashboard, recommendation scoring, or data exports (those are separate,
future scopes; `InteractionSignal` already covers today's recommendation
feature needs — see below).

## Relationship to existing analytics

The codebase already had `InteractionSignal` (`backend/app/models/analytics.py`,
table `interaction_signals`) — a narrow, numeric append-only log purpose-built
for recommendation feature extraction (views/watch-time/completion/rewatch/
like/unlike/save/unsave/share/follow/unfollow). It is **not replaced**.

`AnalyticsEvent` (table `analytics_events`) is a broader, general-purpose
product-analytics event log with free-form JSON metadata, an optional session
id, and millisecond duration, covering interactions `InteractionSignal` was
never designed for (impressions, skips, uploads, publishing, sounds, search,
notifications, collaborations). Several resolvers now record **both** — e.g.
liking a post still writes an `InteractionSignal` (for recommendation
scoring) *and* an `AnalyticsEvent` (for general analytics).

## Event types

Defined in `backend/app/models/analytics.py::EventType`:

| Event type            | Fired when |
|------------------------|------------|
| `VIDEO_IMPRESSION`      | A batch of posts is returned by the Following or For You feed (bulk-recorded per page). |
| `VIDEO_VIEWED`          | `trackPostWatch` is called (a watch session was reported). |
| `VIDEO_WATCHED`         | Same as above; carries `duration_ms` = reported watched time. |
| `VIDEO_COMPLETED`       | `trackPostWatch` reports a server-verified completion (>= 90% of duration). |
| `VIDEO_SKIPPED`         | `trackPostWatch` reports watched time < 25% of the post's duration and not completed. |
| `LIKE_CREATED` / `LIKE_REMOVED` | `likePost`/`unlikePost` mutation succeeds. |
| `COMMENT_CREATED`       | `createComment`/`addComment` mutation succeeds. |
| `SHARE_CREATED`         | `sharePost` mutation succeeds. |
| `SAVE_CREATED`          | `savePost` mutation succeeds. |
| `FOLLOW_CREATED` / `FOLLOW_REMOVED` | `follow`/`unfollow` mutation succeeds. |
| `PROFILE_VIEWED`        | The `profile` query resolves another user's profile (not the viewer's own). |
| `VIDEO_UPLOADED`        | A video file finishes uploading via `POST /media/posts/{post_id}`. |
| `VIDEO_PUBLISHED`       | A post's status becomes `published` (on create, or via `updatePost`/legacy update transitioning from a non-published status). |
| `SOUND_USED`            | A post is created with a non-default `audio` value. |
| `SEARCH_PERFORMED`      | The `search` query runs a non-empty query. Only aggregate metadata (result count, requested types) is stored — **never the raw query text**. |
| `COLLAB_CREATED`        | `createCollaboration` mutation succeeds. |
| `NOTIFICATION_OPENED`   | `markNotificationRead` mutation succeeds. |

## Model

`AnalyticsEvent` (table `analytics_events`, migration `008_analytics_events.py`):

| Column           | Type      | Notes |
|-------------------|-----------|-------|
| `id`               | UUIDv7    | PK |
| `user_id`          | UUID, nullable | FK → `users.id` (`ON DELETE SET NULL`); null = anonymous event |
| `event_type`       | enum, required | one of `EventType` |
| `post_id`          | UUID, nullable | FK → `posts.id` (`ON DELETE SET NULL`) |
| `target_user_id`   | UUID, nullable | FK → `users.id` (`ON DELETE SET NULL`); e.g. the followed/viewed user |
| `session_id`       | string(64), nullable | see "Session identifier" below |
| `duration_ms`      | int, nullable | e.g. watch duration |
| `metadata`         | JSONB, nullable | free-form, sanitized (see Privacy) — Python attribute name is `event_metadata` |
| `created_at` / `updated_at` | timestamp | standard `TimestampMixin` |

Indexes: `user_id`, `event_type`, `post_id`, `target_user_id`, `session_id`,
`created_at` — one single-column index per field named in the requirements,
no composite indexes added speculatively.

## Recording an event

**Never construct `AnalyticsEvent` rows directly.** Always go through
`backend/services/analytics_event_service.py::AnalyticsEventService`:

```python
from app.models.analytics import EventType
from services.analytics_event_service import AnalyticsEventService

await AnalyticsEventService(ctx.db).track_event(
    event_type=EventType.VIDEO_VIEWED,
    user=ctx.current_user,      # optional — None for anonymous events
    post=post,                   # optional
    target_user=target_user,     # optional
    session_id=ctx.session_id,   # optional
    duration_ms=1500,             # optional
    metadata={"source": "for_you_feed"},  # optional, sanitized
)
```

For feed pages, use the batched variant instead of one call per post:

```python
await AnalyticsEventService(ctx.db).track_impressions_bulk(
    user=user, posts=page, session_id=ctx.session_id,
)
```

The service:
- validates `event_type` is a real `EventType` member and `duration_ms` is a
  non-negative int, rejecting (returning `None`, no exception) otherwise;
- accepts `None` for `user`/`post`/`target_user` and safely extracts `.id`;
- strips a denylist of sensitive-looking keys from `metadata` (password/
  token/card number/etc.) as defense in depth;
- **never raises** — any exception while writing is logged
  (`analytics_event.record_failed` / `analytics_event.bulk_record_failed`)
  and swallowed, so a broken analytics write can never break the like/
  follow/comment/etc. action it's attached to.

## Session identifier

No dedicated "analytics session" concept existed. Rather than building a new
one, `AppContext.session_id` reuses the current JWT access token's `jti`
claim (already generated per login/token-refresh in
`features/auth/jwt.py`) as a lightweight, request-scoped session identifier.
It's populated once in `create_graphql_router`'s `get_context`. This is
separate from the durable `Session` table (`app/models/user.py`), which
exists for refresh-token audit/revocation, not analytics.

## Privacy / data minimization

- No passwords, tokens, payment info, private message contents, or raw
  request bodies are ever passed into `metadata`; the service also strips a
  denylist of sensitive-looking keys as a safety net.
- `SEARCH_PERFORMED` stores only aggregate metadata (result count, requested
  result types) — the raw search query text is intentionally **not** stored.
- Uploaded media contents/raw files are never stored in analytics; only
  `file_size_bytes` metadata is recorded for `VIDEO_UPLOADED`.

## Performance

- Each `track_event` call is a single-row insert on the same DB session
  already in use by the resolver (no extra connection), committed alongside
  the primary action's own commit.
- Feed impressions use `track_impressions_bulk`, which is a single batched
  insert for the whole page rather than one write per post.
- No new background job/queue infrastructure was introduced (none existed);
  this matches the project's current lightweight synchronous-write pattern
  used by `InteractionSignal`.

## Not yet integrated / follow-up

- **Frontend instrumentation**: the frontend needs to actually call
  `trackPostWatch` at appropriate impression/view/skip moments for the
  `VIDEO_IMPRESSION`/`VIDEO_VIEWED`/`VIDEO_SKIPPED` semantics above to be
  accurate — this task only wires the backend recording path.
- `VIDEO_SKIPPED` uses a simple heuristic (watched < 25% of duration, not
  completed); this threshold may need tuning once real watch-time
  distributions are available.
- Read-side analytics APIs (creator dashboard, platform dashboard,
  recommendation scoring, exports) are explicitly out of scope for this
  task and not implemented against `AnalyticsEvent`.
