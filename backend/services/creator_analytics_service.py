"""Database-backed creator analytics aggregation.

Uses ``InteractionSignal`` (via ``AnalyticsRepository``) as the source of truth
for engagement signals, complemented by ``Post`` and ``Comment`` queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import SignalType
from app.models.collaboration import CollaborationStatus
from app.models.content import ContentStatus, Post
from repositories.analytics_repository import AnalyticsRepository
from repositories.collaboration_repository import CollaborationRepository
from repositories.content_repository import CommentRepository


class CreatorAnalyticsService:
    """Aggregates analytics for one authenticated creator."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.analytics_repo = AnalyticsRepository(db)

    async def _posts(self, creator_id: uuid.UUID) -> list[Post]:
        result = await self.db.execute(
            select(Post)
            .where(
                Post.user_id == creator_id,
                Post.deleted_at.is_(None),
                Post.status == ContentStatus.PUBLISHED,
            )
            .order_by(Post.published_at.desc(), Post.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    def _growth_pct(current: int | float, previous: int | float) -> float | None:
        if previous == 0:
            return None if current == 0 else 100.0
        return (current - previous) / previous * 100

    async def _period_totals(self, creator_id: uuid.UUID, start: datetime, end: datetime) -> dict:
        signals = await self.analytics_repo.signal_totals(
            creator_id=creator_id, start=start, end=end
        )

        def signal_cnt(*types: SignalType) -> int:
            return sum(int(signals.get(t, {}).get("count", 0)) for t in types)

        views = signal_cnt(SignalType.VIEW, SignalType.REWATCH)
        likes = max(0, signal_cnt(SignalType.LIKE) - signal_cnt(SignalType.UNLIKE))
        saves = max(0, signal_cnt(SignalType.SAVE) - signal_cnt(SignalType.UNSAVE))
        shares = signal_cnt(SignalType.SHARE)
        gained = signal_cnt(SignalType.FOLLOW)
        lost = signal_cnt(SignalType.UNFOLLOW)
        completions = signal_cnt(SignalType.COMPLETION)
        watch_duration_info = signals.get(SignalType.WATCH_DURATION, {})
        watch_sec = float(watch_duration_info.get("total", 0.0))

        try:
            comments = await CommentRepository(self.db).count_for_creator(
                creator_id, start=start, end=end
            )
            total_comments = int(comments) if isinstance(comments, (int, float)) else 0
        except Exception:
            total_comments = 0

        engagements = likes + total_comments + shares + saves
        return {
            "views": views,
            "unique_viewers": signal_cnt(SignalType.VIEW),
            "likes": likes,
            "comments": total_comments,
            "shares": shares,
            "saves": saves,
            "new_followers": gained,
            "lost_followers": lost,
            "follower_growth": gained - lost,
            "completions": completions,
            "watch_sec": watch_sec,
            "engagements": engagements,
            "avg_watch_time": watch_sec / views if views > 0 else None,
            "completion_rate": completions / views * 100 if views > 0 else None,
            "engagement_rate": engagements / views * 100 if views > 0 else 0.0,
        }

    @staticmethod
    def _parse_timestamp(value) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    async def _collaboration_totals(
        self, creator_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict:
        collaborations = await CollaborationRepository(self.db).get_for_user_in_period(
            creator_id, start, end
        )
        accepted_statuses = {
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
            CollaborationStatus.COMPLETED,
        }
        pending = sum(collaboration.status == CollaborationStatus.PROPOSED for collaboration in collaborations)
        accepted = sum(collaboration.status in accepted_statuses for collaboration in collaborations)
        declined = sum(collaboration.status == CollaborationStatus.DECLINED for collaboration in collaborations)
        active = sum(collaboration.status == CollaborationStatus.IN_PROGRESS for collaboration in collaborations)
        completed = sum(collaboration.status == CollaborationStatus.COMPLETED for collaboration in collaborations)
        cancelled = sum(collaboration.status == CollaborationStatus.CANCELLED for collaboration in collaborations)
        response_hours = []
        for collaboration in collaborations:
            request_at = self._parse_timestamp(getattr(collaboration, "proposed_at", None))
            request_at = request_at or getattr(collaboration, "created_at", None)
            if request_at is None:
                continue
            accepted_at = [
                self._parse_timestamp(getattr(participant, "accepted_at", None))
                for participant in getattr(collaboration, "participants", [])
            ]
            accepted_at = [timestamp for timestamp in accepted_at if timestamp is not None]
            if accepted_at:
                elapsed = (min(accepted_at) - request_at).total_seconds() / 3600
                if elapsed >= 0:
                    response_hours.append(elapsed)
        average_response_hours = sum(response_hours) / len(response_hours) if response_hours else None
        return {
            "total_collaboration_requests": len(collaborations),
            "pending_collaborations": pending,
            "accepted_collaborations": accepted,
            "declined_collaborations": declined,
            "active_collaborations": active,
            "completed_collaborations": completed,
            "cancelled_collaborations": cancelled,
            "collaboration_acceptance_rate": accepted / len(collaborations) * 100 if collaborations else None,
            "collaboration_completion_rate": completed / accepted * 100 if accepted else None,
            "collaboration_success_rate": completed / accepted * 100 if accepted else None,
            "average_response_hours": average_response_hours,
        }

    async def overview(self, creator_id: uuid.UUID, start: datetime, end: datetime) -> dict:
        posts = await self._posts(creator_id)
        current = await self._period_totals(creator_id, start, end)
        period_length = end - start
        previous = await self._period_totals(creator_id, start - period_length, start)
        collaborations = await self._collaboration_totals(creator_id, start, end)

        top_video_perf = await self.video_performance(creator_id, start, end)
        top_video_perf.sort(key=lambda item: item["engagement_rate"], reverse=True)

        return {
            "total_posts": len(posts),
            "total_uploads": len(posts),
            "total_published_videos": len(posts),
            "total_views": current["views"],
            "unique_viewers": current["unique_viewers"],
            "total_likes": current["likes"],
            "total_comments": current["comments"],
            "total_shares": current["shares"],
            "total_saves": current["saves"],
            "new_followers": current["new_followers"],
            "lost_followers": current["lost_followers"],
            "follower_growth": current["follower_growth"],
            "avg_watch_time": current["avg_watch_time"],
            "completion_rate": current["completion_rate"],
            "engagement_rate": current["engagement_rate"],
            "views_growth_pct": self._growth_pct(current["views"], previous["views"]),
            "likes_growth_pct": self._growth_pct(current["likes"], previous["likes"]),
            "comments_growth_pct": self._growth_pct(current["comments"], previous["comments"]),
            "shares_growth_pct": self._growth_pct(current["shares"], previous["shares"]),
            "followers_growth_pct": self._growth_pct(current["new_followers"], previous["new_followers"]),
            **collaborations,
            "top_posts": top_video_perf[:5],
        }

    async def video_performance(self, creator_id: uuid.UUID, start: datetime, end: datetime) -> list[dict]:
        posts = await self._posts(creator_id)
        if not posts:
            return []

        post_ids = [p.id for p in posts]
        per_post_signals = await self.analytics_repo.per_post_signal_totals(
            creator_id=creator_id, post_ids=post_ids, start=start, end=end
        )

        result = []
        for post in posts:
            sig = per_post_signals.get(post.id, {})

            def cnt(*types: SignalType) -> int:
                return sum(int(sig.get(t, {}).get("count", 0)) for t in types)

            views = cnt(SignalType.VIEW, SignalType.REWATCH)
            likes = max(0, cnt(SignalType.LIKE) - cnt(SignalType.UNLIKE))
            saves = max(0, cnt(SignalType.SAVE) - cnt(SignalType.UNSAVE))
            shares = cnt(SignalType.SHARE)
            completions = cnt(SignalType.COMPLETION)
            watch_sec = float(sig.get(SignalType.WATCH_DURATION, {}).get("total", 0.0))
            comments = getattr(post, "comment_count", 0)

            # Fallback to denormalized post counters if no signal rows exist in period
            final_views = views if views > 0 else getattr(post, "view_count", 0)
            final_likes = likes if views > 0 or likes > 0 else getattr(post, "like_count", 0)
            final_shares = shares if views > 0 or shares > 0 else getattr(post, "share_count", 0)
            final_saves = saves if views > 0 or saves > 0 else getattr(post, "save_count", 0)

            engagements = final_likes + comments + final_shares + final_saves
            avg_watch = watch_sec / final_views if final_views > 0 and watch_sec > 0 else None
            comp_rate = completions / final_views * 100 if final_views > 0 and completions > 0 else None
            eng_rate = engagements / final_views * 100 if final_views > 0 else 0.0

            result.append({
                "post": post,
                "views": final_views,
                "unique_viewers": cnt(SignalType.VIEW) or final_views,
                "likes": final_likes,
                "comments": comments,
                "shares": final_shares,
                "saves": final_saves,
                "watch_ms": int(watch_sec * 1000),
                "completed": completions,
                "avg_watch_time": avg_watch,
                "completion_rate": comp_rate,
                "engagement_rate": eng_rate,
                "followers_generated": None,
            })
        return result

    async def daily_trends(self, creator_id: uuid.UUID, start: datetime, end: datetime) -> list[dict]:
        """Return one grouped row per UTC day and tracked metric using InteractionSignal."""
        rows = await self.analytics_repo.daily_signal_totals(
            creator_id=creator_id, start=start, end=end
        )
        collaborations = await CollaborationRepository(self.db).get_for_user_in_period(
            creator_id, start, end
        )
        by_date = {row["date"]: row for row in rows}
        accepted_statuses = {
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
            CollaborationStatus.COMPLETED,
        }
        for collaboration in collaborations:
            date_key = collaboration.created_at.date().isoformat()
            point = by_date.setdefault(date_key, {})
            point["collaborations_requested"] = point.get("collaborations_requested", 0) + 1
            point["collaborations_pending"] = point.get("collaborations_pending", 0) + int(
                collaboration.status == CollaborationStatus.PROPOSED
            )
            point["collaborations_accepted"] = point.get("collaborations_accepted", 0) + int(
                collaboration.status in accepted_statuses
            )
            point["collaborations_declined"] = point.get("collaborations_declined", 0) + int(
                collaboration.status == CollaborationStatus.DECLINED
            )
            point["collaborations_completed"] = point.get("collaborations_completed", 0) + int(
                collaboration.status == CollaborationStatus.COMPLETED
            )
            point["collaborations_cancelled"] = point.get("collaborations_cancelled", 0) + int(
                collaboration.status == CollaborationStatus.CANCELLED
            )
        points = []
        current_day = start.date()
        end_day = end.date()
        while current_day <= end_day:
            date_key = current_day.isoformat()
            points.append({
                "date": date_key,
                "views": 0,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "saves": 0,
                "followers_gained": 0,
                "collaborations_requested": 0,
                "collaborations_pending": 0,
                "collaborations_accepted": 0,
                "collaborations_declined": 0,
                "collaborations_completed": 0,
                "collaborations_cancelled": 0,
                **by_date.get(date_key, {}),
            })
            current_day += timedelta(days=1)
        return points