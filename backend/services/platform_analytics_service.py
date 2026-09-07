"""Aggregate platform-wide analytics for internal administrators.

This service reads the existing ``AnalyticsEvent`` log and core tables. It
returns grouped aggregates only; individual events and viewer identities never
leave the database layer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent, EventType
from app.models.content import ContentStatus, Post
from app.models.user import User, UserRole


ENGAGEMENT_EVENTS = (
    EventType.LIKE_CREATED,
    EventType.COMMENT_CREATED,
    EventType.SHARE_CREATED,
    EventType.SAVE_CREATED,
)


class PlatformAnalyticsService:
    """Database aggregation layer for platform analytics."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _period(stmt: Any, start: datetime, end: datetime):
        if start > end:
            raise ValueError("Analytics period start must be before its end")
        return stmt.where(AnalyticsEvent.created_at >= start, AnalyticsEvent.created_at <= end)

    async def _event_totals(self, start: datetime, end: datetime) -> dict:
        stmt = self._period(
            select(
                AnalyticsEvent.event_type,
                func.count().label("count"),
                func.count(distinct(AnalyticsEvent.user_id)).label("unique_users"),
                func.sum(AnalyticsEvent.duration_ms).label("duration_ms"),
            ).group_by(AnalyticsEvent.event_type),
            start,
            end,
        )
        result = await self.db.execute(stmt)
        return {
            row.event_type: {
                "count": int(row.count or 0),
                "unique_users": int(row.unique_users or 0),
                "duration_ms": int(row.duration_ms or 0),
            }
            for row in result.all()
        }

    async def _active_users_between(self, start: datetime, end: datetime) -> int:
        result = await self.db.execute(
            self._period(
                select(func.count(distinct(AnalyticsEvent.user_id))).where(
                    AnalyticsEvent.user_id.is_not(None)
                ),
                start,
                end,
            )
        )
        return int(result.scalar_one() or 0)

    async def _active_creators_between(self, start: datetime, end: datetime) -> int:
        result = await self.db.execute(
            self._period(
                select(func.count(distinct(AnalyticsEvent.user_id)))
                .join(User, User.id == AnalyticsEvent.user_id)
                .where(
                    AnalyticsEvent.user_id.is_not(None),
                    User.role == UserRole.CREATOR,
                ),
                start,
                end,
            )
        )
        return int(result.scalar_one() or 0)

    @staticmethod
    def _growth_pct(current: int | float, previous: int | float) -> float | None:
        if previous == 0:
            return None if current == 0 else 100.0
        return (current - previous) / previous * 100

    async def overview(self, start: datetime, end: datetime) -> dict:
        if start > end:
            raise ValueError("Analytics period start must be before its end")

        total_users_result = await self.db.execute(
            select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        )
        new_users_result = await self.db.execute(
            select(func.count()).select_from(User).where(
                User.deleted_at.is_(None), User.created_at >= start, User.created_at <= end
            )
        )
        total_creators_result = await self.db.execute(
            select(func.count()).select_from(User).where(
                User.deleted_at.is_(None), User.role == UserRole.CREATOR
            )
        )
        new_creators_result = await self.db.execute(
            select(func.count()).select_from(User).where(
                User.deleted_at.is_(None), User.role == UserRole.CREATOR,
                User.created_at >= start, User.created_at <= end,
            )
        )
        active_users = await self._active_users_between(start, end)
        active_creators = await self._active_creators_between(start, end)
        daily_active_users = await self._active_users_between(end - timedelta(days=1), end)
        weekly_active_users = await self._active_users_between(end - timedelta(days=7), end)
        monthly_active_users = await self._active_users_between(end - timedelta(days=30), end)
        events = await self._event_totals(start, end)

        previous_start = start - (end - start)
        previous_end = start
        previous_events = await self._event_totals(previous_start, previous_end)
        previous_new_users_result = await self.db.execute(
            select(func.count()).select_from(User).where(
                User.deleted_at.is_(None), User.created_at >= previous_start, User.created_at <= previous_end
            )
        )
        previous_new_creators_result = await self.db.execute(
            select(func.count()).select_from(User).where(
                User.deleted_at.is_(None), User.role == UserRole.CREATOR,
                User.created_at >= previous_start, User.created_at <= previous_end,
            )
        )
        previous_active_users = await self._active_users_between(previous_start, previous_end)

        def count(event_type: EventType) -> int:
            return events.get(event_type, {}).get("count", 0)

        views = count(EventType.VIDEO_VIEWED)
        likes = count(EventType.LIKE_CREATED)
        comments = count(EventType.COMMENT_CREATED)
        shares = count(EventType.SHARE_CREATED)
        saves = count(EventType.SAVE_CREATED)
        engagements = likes + comments + shares + saves
        qualifying_views = views
        watch_ms = events.get(EventType.VIDEO_WATCHED, {}).get("duration_ms", 0)
        completions = count(EventType.VIDEO_COMPLETED)
        published = count(EventType.VIDEO_PUBLISHED)
        previous_count = lambda event_type: previous_events.get(event_type, {}).get("count", 0)
        previous_views = previous_count(EventType.VIDEO_VIEWED)
        previous_engagements = sum(previous_count(event) for event in ENGAGEMENT_EVENTS)

        moderation_result = await self.db.execute(
            select(Post.moderation_status, func.count().label("count"))
            .where(Post.deleted_at.is_(None))
            .group_by(Post.moderation_status)
        )
        moderation_counts = {row.moderation_status: int(row.count or 0) for row in moderation_result.all()}
        total_users = int(total_users_result.scalar_one() or 0)
        new_users = int(new_users_result.scalar_one() or 0)
        total_creators = int(total_creators_result.scalar_one() or 0)
        new_creators = int(new_creators_result.scalar_one() or 0)
        previous_new_users = int(previous_new_users_result.scalar_one() or 0)
        previous_new_creators = int(previous_new_creators_result.scalar_one() or 0)

        return {
            "total_users": total_users,
            "new_users": new_users,
            "total_creators": total_creators,
            "new_creators": new_creators,
            "active_users": active_users,
            "active_creators": active_creators,
            "daily_active_users": daily_active_users,
            "weekly_active_users": weekly_active_users,
            "monthly_active_users": monthly_active_users,
            "total_uploads": count(EventType.VIDEO_UPLOADED),
            "total_published_videos": published,
            "total_views": views,
            "unique_viewers": events.get(EventType.VIDEO_VIEWED, {}).get("unique_users", 0),
            "total_likes": likes,
            "total_comments": comments,
            "total_shares": shares,
            "total_saves": saves,
            "profile_views": count(EventType.PROFILE_VIEWED),
            "sounds_used": count(EventType.SOUND_USED),
            "collabs_created": count(EventType.COLLAB_CREATED),
            "follows_created": count(EventType.FOLLOW_CREATED),
            "follows_removed": count(EventType.FOLLOW_REMOVED),
            "net_followers": count(EventType.FOLLOW_CREATED) - count(EventType.FOLLOW_REMOVED),
            "average_views_per_published_video": views / published if published else None,
            "engagement_rate": engagements / views * 100 if views else 0.0,
            "average_watch_time": watch_ms / qualifying_views / 1000 if qualifying_views and watch_ms >= 0 else None,
            "completion_rate": completions / qualifying_views * 100 if qualifying_views else None,
            "feed_impressions": count(EventType.VIDEO_IMPRESSION),
            "video_completions": completions,
            "video_skips": count(EventType.VIDEO_SKIPPED),
            "searches": count(EventType.SEARCH_PERFORMED),
            "searchers": events.get(EventType.SEARCH_PERFORMED, {}).get("unique_users", 0),
            "notifications_opened": count(EventType.NOTIFICATION_OPENED),
            "notifications_generated": None,
            "notification_open_rate": None,
            "approved_content": moderation_counts.get("approved", 0),
            "flagged_content": moderation_counts.get("flagged", 0),
            "removed_content": moderation_counts.get("removed", 0),
            "comparison": {
                "user_growth_pct": self._growth_pct(new_users, previous_new_users),
                "creator_growth_pct": self._growth_pct(new_creators, previous_new_creators),
                "content_growth_pct": self._growth_pct(count(EventType.VIDEO_PUBLISHED), previous_count(EventType.VIDEO_PUBLISHED)),
                "views_growth_pct": self._growth_pct(views, previous_views),
                "engagement_growth_pct": self._growth_pct(engagements, previous_engagements),
                "active_users_growth_pct": self._growth_pct(active_users, previous_active_users),
            },
        }

    async def daily_trends(self, start: datetime, end: datetime) -> list[dict]:
        day = func.date(AnalyticsEvent.created_at).label("day")
        stmt = self._period(
            select(day, AnalyticsEvent.event_type, func.count().label("count"))
            .group_by(day, AnalyticsEvent.event_type),
            start,
            end,
        )
        result = await self.db.execute(stmt)
        keys = {
            EventType.VIDEO_UPLOADED: "uploads",
            EventType.VIDEO_PUBLISHED: "published_videos",
            EventType.VIDEO_VIEWED: "views",
            EventType.VIDEO_COMPLETED: "completed_views",
            EventType.LIKE_CREATED: "likes",
            EventType.COMMENT_CREATED: "comments",
            EventType.SHARE_CREATED: "shares",
            EventType.SAVE_CREATED: "saves",
            EventType.FOLLOW_CREATED: "follows",
            EventType.FOLLOW_REMOVED: "follows_removed",
            EventType.PROFILE_VIEWED: "profile_views",
            EventType.COLLAB_CREATED: "collabs",
            EventType.VIDEO_IMPRESSION: "impressions",
            EventType.SEARCH_PERFORMED: "searches",
            EventType.NOTIFICATION_OPENED: "notifications_opened",
        }
        grouped: dict[str, dict] = defaultdict(lambda: {
            "uploads": 0, "published_videos": 0, "views": 0, "completed_views": 0,
            "likes": 0, "comments": 0, "shares": 0, "saves": 0, "follows": 0,
            "follows_removed": 0, "profile_views": 0, "collabs": 0, "impressions": 0,
            "searches": 0, "notifications_opened": 0,
        })
        for row in result.all():
            key = keys.get(row.event_type)
            if key:
                grouped[str(row.day)][key] = int(row.count or 0)
        for values in grouped.values():
            values["net_follows"] = values["follows"] - values["follows_removed"]
            values["engagement"] = values["likes"] + values["comments"] + values["shares"] + values["saves"]
        points = []
        current_day = start.date()
        end_day = end.date()
        while current_day <= end_day:
            date_key = current_day.isoformat()
            values = grouped.get(date_key, {
                "uploads": 0, "published_videos": 0, "views": 0, "completed_views": 0,
                "likes": 0, "comments": 0, "shares": 0, "saves": 0, "follows": 0,
                "follows_removed": 0, "profile_views": 0, "collabs": 0, "impressions": 0,
                "searches": 0, "notifications_opened": 0, "net_follows": 0, "engagement": 0,
            })
            points.append({"date": date_key, **values})
            current_day += timedelta(days=1)
        return points

    @staticmethod
    def _normalize_sort_key(sort_by: str | None) -> str:
        if sort_by is None:
            return "views"
        normalized = sort_by.strip().lower().replace("-", "_")
        aliases = {
            "views": "views",
            "likes": "likes",
            "comments": "comments",
            "shares": "shares",
            "saves": "saves",
            "engagement": "engagement",
            "engagementrate": "engagement",
            "engagement_rate": "engagement",
            "completion": "completion_rate",
            "completionrate": "completion_rate",
            "completion_rate": "completion_rate",
        }
        return aliases.get(normalized, "views")

    async def top_content(self, start: datetime, end: datetime, sort_by: str = "views", limit: int = 10) -> list[dict]:
        metric = {
            "views": EventType.VIDEO_VIEWED,
            "likes": EventType.LIKE_CREATED,
            "comments": EventType.COMMENT_CREATED,
            "shares": EventType.SHARE_CREATED,
            "saves": EventType.SAVE_CREATED,
        }.get(self._normalize_sort_key(sort_by), EventType.VIDEO_VIEWED)
        stmt = self._period(
            select(
                AnalyticsEvent.post_id,
                AnalyticsEvent.event_type,
                func.count().label("count"),
                func.count(distinct(AnalyticsEvent.user_id)).label("unique_users"),
                func.sum(AnalyticsEvent.duration_ms).label("duration_ms"),
            )
            .join(Post, Post.id == AnalyticsEvent.post_id)
            .where(
                Post.deleted_at.is_(None),
                Post.status == ContentStatus.PUBLISHED,
                AnalyticsEvent.post_id.is_not(None),
            )
            .group_by(AnalyticsEvent.post_id, AnalyticsEvent.event_type)
            .order_by(func.sum(case((AnalyticsEvent.event_type == metric, 1), else_=0)).desc())
            .limit(1000),
            start,
            end,
        )
        result = await self.db.execute(stmt)
        grouped_rows = list(result.all())
        grouped: dict[Any, dict] = defaultdict(lambda: {
            "views": 0, "unique_viewers": 0, "likes": 0, "comments": 0,
            "shares": 0, "saves": 0, "watch_ms": 0, "completed": 0, "post": None,
        })
        posts_result = await self.db.execute(
            select(Post).where(Post.id.in_([row.post_id for row in grouped_rows]))
        )
        scalar_rows = getattr(posts_result, "scalars", None)
        if callable(scalar_rows):
            posts = {post.id: post for post in scalar_rows().all()}
        else:
            posts = {post.id: post for post in posts_result.all()}
        for row in grouped_rows:
            item = grouped[row.post_id]
            item["post"] = posts.get(row.post_id)
            key = {
                EventType.VIDEO_VIEWED: "views", EventType.LIKE_CREATED: "likes",
                EventType.COMMENT_CREATED: "comments", EventType.SHARE_CREATED: "shares",
                EventType.SAVE_CREATED: "saves",
            }.get(row.event_type)
            if key:
                item[key] += int(row.count or 0)
            if row.event_type == EventType.VIDEO_VIEWED:
                item["unique_viewers"] += int(row.unique_users or 0)
            elif row.event_type == EventType.VIDEO_WATCHED:
                item["watch_ms"] += int(row.duration_ms or 0)
            elif row.event_type == EventType.VIDEO_COMPLETED:
                item["completed"] += int(row.count or 0)
        rows = []
        for item in grouped.values():
            views = item["views"]
            engagements = item["likes"] + item["comments"] + item["shares"] + item["saves"]
            item["engagement_rate"] = engagements / views * 100 if views else 0.0
            item["completion_rate"] = item["completed"] / views * 100 if views else None
            item["average_watch_time"] = item["watch_ms"] / views / 1000 if views else None
            rows.append(item)
        sort_key = self._normalize_sort_key(sort_by)
        rows.sort(
            key=lambda row: (
                row["engagement_rate"] if sort_key == "engagement" else
                (row["completion_rate"] or 0) if sort_key == "completion_rate" else
                row.get(sort_key, 0)
            ),
            reverse=True,
        )
        return rows[: max(0, min(limit, 10))]