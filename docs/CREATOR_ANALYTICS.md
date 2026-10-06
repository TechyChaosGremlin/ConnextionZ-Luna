# Creator Analytics v1

Creator analytics is available to the authenticated creator through the existing GraphQL API. The supported windows are supplied as an `AnalyticsPeriod` with UTC `start` and `end` timestamps; the frontend defaults to 28 days and supports 7, 28, and 90 days.

## Queries

- `creatorAnalytics(period)` returns overview totals.
- `creatorVideoAnalytics(period, sortBy)` returns at most 100 published posts, aggregated on the backend. Supported sort values are `recent`, `views`, `likes`, `comments`, `shares`, `saves`, `engagement`, and `completion`.
- `creatorAnalyticsTrends(period)` returns daily chart points.

## Formulas and events

Creator engagement totals use the existing `InteractionSignal` aggregates, scoped by creator and requested period. Rates use event counts, not distinct viewer counts or summed signal values.

- Views: `VIEW + REWATCH` events.
- Unique viewers: distinct `user_id` values across `VIEW` and `REWATCH` signals, counted once across the creator's posts.
- Unique viewer rate (`uniqueViewerRate`): distinct unique viewers divided by `VIEW + REWATCH` event count, multiplied by 100; null when there are no views.
- Rewatches (`totalRewatches`): `REWATCH` event count, including repeated events from the same viewer.
- Rewatch rate (`rewatchRate`): `REWATCH / (VIEW + REWATCH) * 100`.
- Unique not-interested users (`uniqueNotInterestedUsers`): distinct users with at least one `NOT_INTERESTED` signal for the creator in the requested period. A user's feedback on multiple posts counts once; the mutation is idempotent per user and post. Returns `0` without qualifying signals. This measures recorded explicit feedback, not all viewers who disliked or skipped content.
- Likes and saves: `max(0, LIKE - UNLIKE)` and `max(0, SAVE - UNSAVE)` event counts; shares use `SHARE` event counts. Comments use the existing creator comment query.
- Unlike and unsave totals (`totalUnlikes`, `totalUnsaves`): successful `UNLIKE` and `UNSAVE` state-transition events, respectively. Unique actors (`uniqueUnlikers`, `uniqueUnsavers`) count distinct users with at least one corresponding event in the period; these are activity counts, not current post state.
- Unique rewatchers and rewatch rate (`uniqueRewatchers`, `uniqueRewatchRate`): distinct users with a `REWATCH` signal, and that count divided by unique viewers. Repeated rewatches count once per user; the rate is null with no unique viewers.
- Unique likers (`uniqueLikers`): distinct `InteractionSignal.user_id` values with at least one `LIKE` signal for the creator within the requested period (inclusive start and end). Repeated likes, re-likes, and likes across multiple posts count once per user. Later unlikes do not remove users from this activity count; it is not the current number of users who still like a post. Self-likes are included, and the count is scoped by the persisted creator ID without filtering current post status, matching unique sharers. Returns `0` without qualifying signals; historical likes without tracked signals cannot be reconstructed.
- Unique commenters (`uniqueCommenters`): distinct `Comment.user_id` values on the creator's non-deleted comments within the requested period. Replies are included.
- Daily comment trends count persisted, non-deleted comments on posts owned by the creator, including replies. Counts are grouped by UTC calendar day within the inclusive requested period; days without comments return zero. Their sum matches the period comment total because both use the same comment ownership, deletion, and timestamp filters.
- Per-video comments (`creatorVideoAnalytics.comments`): period-scoped counts of persisted, non-deleted `Comment` rows on that specific post, including replies, created within the inclusive requested period. The denormalized all-time `Post.comment_count` is not used. Videos without qualifying comments return `0`, and the `comments` sort and per-video engagement rate use these period-scoped counts.
- Save rate (`saveRate`): `max(0, SAVE - UNSAVE) / (VIEW + REWATCH) * 100`. This is net save activity within the period, not distinct savers or the current saved-state total.
- Unique savers (`uniqueSavers`): distinct `InteractionSignal.user_id` values with at least one `SAVE` signal for the creator within the requested period (inclusive start and end). Repeated saves, re-saves, and saves across multiple posts count once per user. Later unsaves do not remove users from this activity count; it is not the current number of users with saved posts. Self-saves are included, and the count uses the persisted creator ID without filtering current post status, matching the other distinct signal-actor counts. Returns `0` without qualifying signals; historical saves without tracked signals cannot be reconstructed.
- Share rate (`shareRate`): `SHARE / (VIEW + REWATCH) * 100`. This uses share events within the period, not distinct sharers, and is not capped at 100%.
- Unique sharers (`uniqueSharers`): distinct users with at least one newly-created `SHARE` signal for the creator during the requested period. Repeated shares by one user count once.
- Profile views and unique profile viewers: profile-view `AnalyticsEvent` counts and distinct viewers in the requested period. `uniqueProfileViewerRate` is `uniqueProfileViewers / profileViews * 100`, and is null when there are no profile views.
- Feed impressions and unique impression viewers: `VIDEO_IMPRESSION` event counts and distinct viewers in the requested period.
- Video skips and skip rate: `VIDEO_SKIPPED` counts and `VIDEO_SKIPPED / VIDEO_VIEWED * 100`.
- Follow signals include the target creator ID and event timestamp. `FOLLOW` is recorded only when a relationship is newly created; `UNFOLLOW` is recorded only when an existing relationship is actually removed. Repeated/no-op unfollows remain successful but emit neither an `UNFOLLOW` signal nor a `FOLLOW_REMOVED` analytics event. Historical signals/events recorded before this fix may include no-op unfollows; the current follow table cannot reconstruct deleted relationships or reliably repair those historical counts.
- Unique new followers (`uniqueNewFollowers`): distinct users with a newly-created `FOLLOW` transition in the requested period. A user who follows, unfollows, and follows again counts once in this distinct-actor metric.
- Current followers (`currentFollowers`) is a point-in-time count of live rows in the canonical `follows` table; unlike period-based gained/lost follower signals, this reflects current relationships.
- Average watch time: summed `WATCH_DURATION` values in seconds divided by views.
- Total completions (`totalCompletions`): `COMPLETION` event count scoped to the persisted creator ID and requested period (inclusive start and end), without filtering current post status. Tracking emits this signal for a watch marked completed only after server validation confirms a positive video duration and at least 90% watched. It does not mean exactly 100% watched or distinct viewers; repeated qualifying watches, including rewatches, each count. Returns `0` without qualifying signals. Historical watches without recorded completion signals cannot be reconstructed.
- Unique completers and unique completion rate (`uniqueCompleters`, `uniqueCompletionRate`): distinct users with at least one validated `COMPLETION` signal, and that count divided by unique viewers. Repeated completions count once per user; the rate is null with no unique viewers.
- Completion rate: `COMPLETION` event count divided by views, multiplied by 100.
- Engagement rate: `(likes + comments + shares + saves) / views * 100`; it is `0` when views are zero.

Rewatch, save, and share rates are null when views are zero. When views exist but the corresponding numerator is zero, the rate is `0`.

## Streaming session metrics

`totalEndedStreamSessions` counts the creator's persisted `StreamSession` rows with status `ended`, valid start/end timestamps, and non-negative duration. `totalBroadcastDuration` sums the full duration of those same sessions in seconds, using a single shared aggregation. Both return zero when no sessions qualify.

The inclusive period applies to `ended_at`, not `created_at` or `started_at`; sessions that began before the period are included if they ended within it, without clipping their duration. Pending, active, failed, other creators' sessions, and sessions with missing or invalid timestamps do not contribute. These are broadcast lifecycle metrics, not viewer/watch analytics or proof of successful delivery to every destination.

`streamDestinationBreakdown` returns `{ platform, endedSessions }` entries sorted by the persisted platform value (`facebook`, `kick`, `twitch`, or `youtube`). It uses the same owner, inclusive session `ended_at` period, ended status, and valid-duration scope as the session totals. Each session counts once per platform even if it has duplicate destination rows; multi-platform sessions count in each platform, so the breakdown sum can exceed `totalEndedStreamSessions`. Destination-row status is not a delivery-success filter. Sessions without destinations remain in the overall totals but have no breakdown entry. No matching destinations returns an empty list, without invented platform buckets.

### Viewer-session collection (analytics not enabled)

Migration `202` adds `stream_viewer_sessions`, linked to the canonical
`StreamSession`, for authenticated viewing connections only. The foundation
stores join time, absolute lease deadline, optional finalized leave time, and
a connection-attempt UUID for join retry idempotency. Multiple connections and
reconnect intervals for one user are allowed. User deletion cascades to these
rows and removes that user's historical audience contribution.

Authenticated REST join, heartbeat, and leave now collect presence through
`AudienceService` and a dedicated repository. The server-configured lease
defaults to 60 seconds; retries do not extend it, and closed/expired intervals
cannot be revived. An expired unclosed interval is inactive without cleanup.
Ownership is the authenticated viewer's identity, not the creator's.
See [API standards](./API_STANDARDS.md#authenticated-viewer-presence) for contracts
and rate limits.

Audience aggregation and new GraphQL metrics remain disabled. Existing
`uniqueViewers`, `totalWatchTime`, and broadcast metrics retain their meanings.
Live presence is not written to `post_watches` or `analytics_events`.

Future audience calculations will count distinct authenticated users and union
overlapping intervals per user before calculating watch duration or peak
concurrency. Intervals are half-open and bounded by the broadcast start/end,
connection leave, lease expiry, and reporting time. An unclosed row alone does
not establish active presence. Lease-backed duration estimates presence, not
verified playback; an unreported disconnect can overcount by the outstanding
lease. The table does not measure anonymous or external-platform audiences.

Future creator integration must scope through `StreamSession.owner_id`, keep
live audience metrics separate from video metrics, and preserve the existing
ended-session period semantics. Unique users must be deduplicated across
selected broadcasts; session peaks must not be summed.

### Native stream follows

`follow(username, streamSessionId)` is the existing creator-follow mutation,
not a second stream event API. Attribution requires an authenticated user with
an unclosed, unexpired viewer lease on that exact **active** Luna stream.
The supplied username must identify `StreamSession.owner_id`; the server derives
the signal's creator from that owner. Pending, ended, failed, future-started,
and already-finalized streams are rejected. Self-follows remain prohibited.
Ordinary follows without `streamSessionId` keep their existing behavior.

`streamAttributedFollows` counts newly created canonical follow transitions
explicitly linked to the creator's streams in **[start, end)**.
`uniqueStreamFollowers` counts distinct actors across those same transitions,
deduplicated across broadcasts and follow/unfollow/re-follow cycles.
Both return zero with no activity. These are acquisition/activity counts, not
current followers or stream subscriptions. The generic follower metrics retain
their existing semantics; stream follows are a subset, never added twice.

The canonical `uq_follow_pair` constraint and `INSERT ... ON CONFLICT DO NOTHING
RETURNING` determine whether to emit a signal. Retrying a follow, or following
while already following from another broadcast, emits no new signal and cannot
reattribute an existing relationship. A genuine follow after an unfollow is a
new transition. There is no timestamp-based inference or historical backfill.
The existing GraphQL action limiter charges every executable `follow`, including
aliases and no-op retries, at 20/user/minute; overflow uses HTTP 429 and
`Retry-After`. No separate limiter is introduced.

Migration `206` (parent `205`) restricts attributed signals to creator-level
follows with no post association, and adds a partial creator/time index.
Upgrade fails if existing rows violate that guard; it does not silently
rewrite or delete historical events.
Migration `203` already provides the StreamSession foreign key; stream deletion
sets attribution to null and removes it from stream reporting. User deletion
cascades signals. Reporting joins through stream ownership, bounds event time,
and excludes ordinary follows and all post or external-platform engagement.
Likes/unlikes, shares, and saves/unsaves remain **unsupported for streams**:
their Luna write paths target posts, not broadcasts. Unfollow is creator-level
and has no originating stream parameter, so it is not inferred as stream activity.

Follow state and its canonical signal remain transactional, matching the
existing architecture: a signal-write failure fails the follow transaction
rather than acknowledging an untracked action. No stream manager, FFmpeg, stop,
or lifecycle operation is called by attribution or reporting. The ancillary
product `FOLLOW_CREATED` event uses a SAVEPOINT for attributed follows: its
failure is logged without rolling back the canonical follow or stream signal.
It is not a second stream-reporting source. Apply pending
migrations before enabling these APIs; offline SQL validation is not deployment.

### Native stream subscriptions

Migration `205` (parent `204`) adds `stream_subscriptions`. Each row identifies
a persisted Luna `StreamSession`, an authenticated subscriber (`user_id`), and
the server-generated creation time. The receiving creator is derived through
`StreamSession.owner_id`, not stored as an independent attribution.

`streamSubscriptions` counts these rows created in **[start, end)**.
`uniqueStreamSubscribers` counts distinct subscribers across all of the
authenticated creator's streams in that same window. Both return zero for no
activity, and neither includes follows, generic creator subscriptions, direct
messages, GraphQL transport subscriptions, or external-platform subscriptions.
These queries are independent of the ended-session broadcast reporting window.

The native subscription action is unique per subscriber and broadcast; retries
return the original row without changing its timestamp. The same subscriber
may subscribe on multiple broadcasts, contributing multiple actions but only
one unique subscriber per creator reporting window. Persisted broadcasts,
including archived broadcasts, are eligible; the action does not require an
active viewer lease. Self-subscription is rejected.

This is a native stream-associated subscription action, **not** a payment,
renewal, recurring billing entitlement, or subscriber balance. There is no
billing/subscription lifecycle model in Luna to link yet. There is no historical
backfill or inferred stream attribution. Hard deletion of the stream or user
cascades to the records and removes their historical contribution. Apply
migration `205` before serving the new write path or analytics queries.

## Collaboration metrics

Collaboration metrics are calculated from non-deleted collaborations involving the authenticated creator and created within the requested period. The response includes total requests, proposed/pending, accepted or progressed (`accepted`, `in_progress`, or `completed`), declined, cancelled, active, and completed counts. Acceptance rate is accepted-or-progressed requests divided by total requests; completion rate is completed requests divided by accepted-or-progressed requests. Both rates are null when their denominator is zero.

Average response time is returned in hours only when an existing `accepted_at` participant timestamp can be parsed; it measures the time from `proposed_at` (falling back to the collaboration `created_at`) to the earliest participant acceptance. Daily trends include request, pending, accepted, declined, completed, and cancelled collaboration counts by creation date.

Missing watch duration and zero qualifying views do not produce fabricated rates. Per-video follower attribution is unavailable because follow events are creator-level and do not identify the originating video.

## Authorization and limitations

Every analytics resolver uses the authenticated user as the creator scope. Raw events, viewer identities, and private data are never returned. Only published, non-deleted creator posts are included. Sound and hashtag performance are unavailable until reliable usage-to-performance attribution is tracked.