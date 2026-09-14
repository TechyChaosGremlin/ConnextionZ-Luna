# Creator Analytics v1

Creator analytics is available to the authenticated creator through the existing GraphQL API. The supported windows are supplied as an `AnalyticsPeriod` with UTC `start` and `end` timestamps; the frontend defaults to 28 days and supports 7, 28, and 90 days.

## Queries

- `creatorAnalytics(period)` returns overview totals.
- `creatorVideoAnalytics(period, sortBy)` returns at most 100 published posts, aggregated on the backend. Supported sort values are `recent`, `views`, `likes`, `comments`, `shares`, `saves`, `engagement`, and `completion`.
- `creatorAnalyticsTrends(period)` returns daily chart points.

## Formulas and events

- Views: `VIDEO_VIEWED` events.
- Unique viewers: distinct non-null `user_id` values on `VIDEO_VIEWED` events.
- Likes, comments, shares, saves: `LIKE_CREATED`, `COMMENT_CREATED`, `SHARE_CREATED`, and `SAVE_CREATED`.
- Followers gained/lost: `FOLLOW_CREATED` and `FOLLOW_REMOVED` where `target_user_id` is the creator.
- Average watch time: summed non-negative `VIDEO_WATCHED.duration_ms` divided by qualifying `VIDEO_VIEWED` events, returned in seconds.
- Completion rate: `VIDEO_COMPLETED` divided by `VIDEO_VIEWED`, multiplied by 100.
- Engagement rate: `(likes + comments + shares + saves) / views * 100`; it is `0` when views are zero.

## Collaboration metrics

Collaboration metrics are calculated from non-deleted collaborations involving the authenticated creator and created within the requested period. The response includes total requests, proposed/pending, accepted or progressed (`accepted`, `in_progress`, or `completed`), declined, cancelled, active, and completed counts. Acceptance rate is accepted-or-progressed requests divided by total requests; completion rate is completed requests divided by accepted-or-progressed requests. Both rates are null when their denominator is zero.

Average response time is returned in hours only when an existing `accepted_at` participant timestamp can be parsed; it measures the time from `proposed_at` (falling back to the collaboration `created_at`) to the earliest participant acceptance. Daily trends include request, pending, accepted, declined, completed, and cancelled collaboration counts by creation date.

Missing watch duration and zero qualifying views do not produce fabricated rates. Per-video follower attribution is unavailable because follow events are creator-level and do not identify the originating video.

## Authorization and limitations

Every analytics resolver uses the authenticated user as the creator scope. Raw events, viewer identities, and private data are never returned. Only published, non-deleted creator posts are included. Sound and hashtag performance are unavailable until reliable usage-to-performance attribution is tracked.