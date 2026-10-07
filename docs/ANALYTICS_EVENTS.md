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

## Paid attribution boundary

The [Paid Algorithm Specification](./ARCHITECTURE.md#paid-algorithm-specification)
defines Paid as a separate discovery area, not placements in normal feeds.
Engagement provenance is resolved server-side from the exact impressed
`paid_deliveries` row referenced by the Paid feed item, scoped to authenticated
viewer and post. Paid-specific mutations also require a separate random,
five-minute interaction context issued for that selection. Only its hash is
stored, and it is consumed under a row lock on the first valid interaction.
The delivery ID alone is only a lookup reference: the server rejects unknown,
pending, wrong-viewer, or wrong-post identities, and the context cannot be
forged or reused. Client metadata such as `source`, `algorithm`, `isSponsored`,
or campaign IDs cannot establish Paid attribution. Events without both the
validated delivery reference and interaction context stay unattributed/non-Paid,
even if the same viewer previously received that post as Paid. `AnalyticsEvent`,
`InteractionSignal`, and active post-interaction rows retain the resolved
delivery and campaign IDs; Paid event metadata is populated only after that
lookup succeeds. Invalid explicit references fail safely rather than being
guessed or replaced with the latest Paid receipt.

Only Paid-specific interaction mutations accept a delivery ID and interaction
context. Normal like/save/share/watch/feedback mutations do not accept either,
so a valid Paid delivery ID replayed through a non-Paid interaction route cannot
assign Paid provenance. Paid-specific mutations validate the context and the
delivery ID against the authenticated viewer, post, and impressed ledger row.

Recommendation aggregates for creator affinity, viewer history, For You
engagement rates, Viral momentum, and interest tags exclude rows with
`paid_delivery_id`. For You's counter inputs and view-count diversity tie-break
also use active interaction rows whose Paid attribution is null. Paid events
remain in the analytics/event stores and in analytics totals; they are not
discarded. Historical rows retain null provenance and continue to be treated
as non-Paid, consistent with the public Paid gate having remained disabled.
Campaign budget/cap accounting does not depend on this best-effort event logger.

The Paid resolver has a receipt-backed delivery integration, but the hard-coded
public delivery gate remains disabled; requests still return the explicit
not-implemented error and do not publicly deliver posts. Attribution migration
209 adds nullable provenance columns with no historical backfill. No Paid
impression or spend accounting is copied into analytics; the delivery ledger
remains authoritative.

### Authoritative Paid impressions and frequency history

[PaidFrequencyService](../backend/services/paid_frequency.py) now has a read-only
boundary for per-viewer/per-campaign frequency eligibility. It counts only
distinct, server-validated Paid deliveries in
`[now - frequency_window_seconds, now]` (inclusive bounds), never general watch/engagement activity.
The default [history repository](../backend/repositories/paid_impression_history_repository.py)
reads only `paid_deliveries` rows with `impressed_at` set. Pending server
selections, ordinary `VIDEO_IMPRESSION` events, and campaign/source claims in
free-form metadata are not impressions. The primary key is a server-generated
selection identity, so one ID counts once and a later genuine delivery needs
a new ID. Attribution is derived from that persisted viewer/campaign/post
relationship, not a client-supplied source flag.

[PaidDeliveryService](../backend/services/paid_delivery_service.py) remains the
authoritative accounting boundary: campaign row locking, revalidation,
database-side impression/reach increments, and ledger recording happen
transactionally. The gated GraphQL delivery integration commits accounting
before returning receipt-backed sponsored items and rolls back on failure.
Spend is unchanged; the record establishes serving semantics, not billing or
client-visible/watch verification. No public Paid delivery is enabled.

The history read checks lifetime ledger count/distinct viewers against the
campaign's impression/reach counters. Complete zero history is a valid zero;
missing schema or mismatched/legacy counters remain
`PaidImpressionHistoryUnavailable`, yielding `ATTRIBUTION_UNAVAILABLE` with a
warning/reason for capped frequency. Missing-table queries use a savepoint so
the transaction remains usable; unexpected failures propagate. Uncapped
frequency does not query history, but internal recording always checks lifetime
completeness. Viewer deletion retains the campaign-local UUID/count history.
No backfill from unverifiable old events is performed.

Paid views, watch behavior, likes, saves, shares, and not-interested signals
can carry Paid attribution when their mutation includes the Paid item delivery
ID and one-use interaction context, and the server verifies the exact ledger
receipt. Comments, follows, and purchases do not carry this delivery context
in the current mutation contract.
Existing events and non-Paid behavior remain unchanged.

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
| `FOLLOW_CREATED` / `FOLLOW_REMOVED` | `follow` creates a relationship / `unfollow` actually removes an existing relationship. Repeated no-op requests do not emit events. |
| `PROFILE_VIEWED`        | The `profile` query resolves another user's profile (not the viewer's own). |
| `VIDEO_UPLOADED`        | A video file finishes uploading via `POST /media/posts/{post_id}`. |
| `VIDEO_PUBLISHED`       | A post's status becomes `published` (on create, or via `updatePost`/legacy update transitioning from a non-published status). |
| `SOUND_USED`            | A post is created with a non-default `audio` value. |
| `SEARCH_PERFORMED`      | The `search` query runs a non-empty query. Only aggregate metadata (result count, requested types) is stored — **never the raw query text**. |
| `COLLAB_CREATED`        | `createCollaboration` mutation succeeds. |
| `COLLAB_ACCEPTED`       | `acceptCollaboration` mutation succeeds. |
| `COLLAB_STARTED`        | `updateCollaboration` transitions an accepted collaboration to `in_progress`. |
| `COLLAB_DECLINED`       | `declineCollaboration` mutation succeeds. |
| `COLLAB_CANCELLED`      | `updateCollaboration` mutation successfully transitions a collaboration to cancelled. |
| `COLLAB_COMPLETED`      | `updateCollaboration` mutation successfully transitions a collaboration to completed. |
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
