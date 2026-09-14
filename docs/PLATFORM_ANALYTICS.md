# Platform Analytics v1

Platform Analytics is an internal, aggregate-only administrator surface. The backend requires `UserRole.ADMIN` for every platform analytics query; creator and regular-user analytics access is rejected server-side.

## Supported queries

- `platformAnalytics(period)` returns user, content, engagement, feed, search, notification, and social totals.
- `platformAnalyticsTrends(period)` returns daily aggregate activity.
- `platformTopContent(period, sortBy)` returns at most 10 eligible published posts. Supported sorting is `views`, `likes`, `comments`, `shares`, `saves`, `engagement`, and `completion_rate`.

The UI supports Today, 7 days, 28 days, and 90 days, defaulting to 28 days. Top content can be sorted by views, likes, comments, shares, saves, engagement rate, or completion rate. Trend reporting exposes selectable daily views, engagement, uploads, and published-video series.

## Available metrics and events

Metrics are grouped from `AnalyticsEvent` and core user/post records. Views use `VIDEO_VIEWED`; completions use `VIDEO_COMPLETED`; watch time uses non-negative `VIDEO_WATCHED.duration_ms`; feed activity uses `VIDEO_IMPRESSION`, `VIDEO_SKIPPED`, and the video events. Social totals use the corresponding created events. User totals use the `users` table, while active users are distinct non-null users with analytics activity in the selected period.

Engagement rate is `(likes + comments + shares + saves) / views * 100`, and is `0` when views are zero. Average watch time is returned in seconds. Completion and watch-time rates are null when there are no qualifying views.

## Activity-window semantics

Active users are distinct non-null users with at least one recorded analytics event. Daily active users use the trailing 1-day window, weekly active users use the trailing 7-day window, and monthly active users use the trailing 30-day window, all ending at the selected period end. These are activity-based operational metrics, not durable login-session metrics.

## Unavailable metrics

Notification generation/open rate is unavailable because only notification-open events are currently tracked. Sound views by sound, search-to-interaction conversion, system errors, failed uploads, and background-job failures require additional reliable tracking. No viewer identities, raw event metadata, or private content details are returned.

Aggregations use date predicates and existing analytics indexes. Top content is bounded and only includes non-deleted published posts.
