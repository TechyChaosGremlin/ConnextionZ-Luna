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
- Save rate (`saveRate`): `max(0, SAVE - UNSAVE) / (VIEW + REWATCH) * 100`. This is net save activity within the period, not distinct savers or the current saved-state total.
- Unique savers (`uniqueSavers`): distinct `InteractionSignal.user_id` values with at least one `SAVE` signal for the creator within the requested period (inclusive start and end). Repeated saves, re-saves, and saves across multiple posts count once per user. Later unsaves do not remove users from this activity count; it is not the current number of users with saved posts. Self-saves are included, and the count uses the persisted creator ID without filtering current post status, matching the other distinct signal-actor counts. Returns `0` without qualifying signals; historical saves without tracked signals cannot be reconstructed.
- Share rate (`shareRate`): `SHARE / (VIEW + REWATCH) * 100`. This uses share events within the period, not distinct sharers, and is not capped at 100%.
- Unique sharers (`uniqueSharers`): distinct users with at least one newly-created `SHARE` signal for the creator during the requested period. Repeated shares by one user count once.
- Profile views and unique profile viewers: profile-view `AnalyticsEvent` counts and distinct viewers in the requested period. `uniqueProfileViewerRate` is `uniqueProfileViewers / profileViews * 100`, and is null when there are no profile views.
- Feed impressions and unique impression viewers: `VIDEO_IMPRESSION` event counts and distinct viewers in the requested period.
- Video skips and skip rate: `VIDEO_SKIPPED` counts and `VIDEO_SKIPPED / VIDEO_VIEWED * 100`.
- Follow signals include the target creator ID and event timestamp, but `UNFOLLOW` is recorded even when no follow row was removed. Therefore `FOLLOW` counts are newly-created follow events, while `UNFOLLOW` counts and net follower growth are not reliable relationship-transition counts. The current follow table cannot reconstruct deleted relationships or distinguish these repeated unfollow requests in historical signals.
- Unique new followers (`uniqueNewFollowers`): distinct users with a newly-created `FOLLOW` transition in the requested period. A user who follows, unfollows, and follows again counts once in this distinct-actor metric.
- Current followers (`currentFollowers`) is a point-in-time count of live rows in the canonical `follows` table; unlike period-based gained/lost follower signals, this reflects current relationships.
- Average watch time: summed `WATCH_DURATION` values in seconds divided by views.
- Total completions (`totalCompletions`): `COMPLETION` event count scoped to the persisted creator ID and requested period (inclusive start and end), without filtering current post status. Tracking emits this signal for a watch marked completed only after server validation confirms a positive video duration and at least 90% watched. It does not mean exactly 100% watched or distinct viewers; repeated qualifying watches, including rewatches, each count. Returns `0` without qualifying signals. Historical watches without recorded completion signals cannot be reconstructed.
- Unique completers and unique completion rate (`uniqueCompleters`, `uniqueCompletionRate`): distinct users with at least one validated `COMPLETION` signal, and that count divided by unique viewers. Repeated completions count once per user; the rate is null with no unique viewers.
- Completion rate: `COMPLETION` event count divided by views, multiplied by 100.
- Engagement rate: `(likes + comments + shares + saves) / views * 100`; it is `0` when views are zero.

Rewatch, save, and share rates are null when views are zero. When views exist but the corresponding numerator is zero, the rate is `0`.

## Collaboration metrics

Collaboration metrics are calculated from non-deleted collaborations involving the authenticated creator and created within the requested period. The response includes total requests, proposed/pending, accepted or progressed (`accepted`, `in_progress`, or `completed`), declined, cancelled, active, and completed counts. Acceptance rate is accepted-or-progressed requests divided by total requests; completion rate is completed requests divided by accepted-or-progressed requests. Both rates are null when their denominator is zero.

Average response time is returned in hours only when an existing `accepted_at` participant timestamp can be parsed; it measures the time from `proposed_at` (falling back to the collaboration `created_at`) to the earliest participant acceptance. Daily trends include request, pending, accepted, declined, completed, and cancelled collaboration counts by creation date.

Missing watch duration and zero qualifying views do not produce fabricated rates. Per-video follower attribution is unavailable because follow events are creator-level and do not identify the originating video.

## Authorization and limitations

Every analytics resolver uses the authenticated user as the creator scope. Raw events, viewer identities, and private data are never returned. Only published, non-deleted creator posts are included. Sound and hashtag performance are unavailable until reliable usage-to-performance attribution is tracked.