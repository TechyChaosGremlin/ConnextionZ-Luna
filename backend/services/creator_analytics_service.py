"""Database-backed creator analytics aggregation.

Uses ``InteractionSignal`` (via ``AnalyticsRepository``) as the source of truth
for engagement signals, complemented by ``Post`` and ``Comment`` queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import EventType, SignalType
from app.models.collaboration import CollaborationStatus
from app.models.content import ContentStatus, Post
from repositories.analytics_repository import AnalyticsRepository
from repositories.analytics_event_repository import AnalyticsEventRepository
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
        unique_viewers = await self.analytics_repo.unique_viewer_counts(
            creator_id=creator_id, start=start, end=end
        )
        profile_viewers = await AnalyticsEventRepository(self.db).profile_viewer_counts_for_creator(
            creator_id, start, end
        )
        post_event_totals = await AnalyticsEventRepository(self.db).post_event_totals_for_creator(
            creator_id, start, end
        )
        impression_totals = post_event_totals.get(EventType.VIDEO_IMPRESSION, {})
        skip_totals = post_event_totals.get(EventType.VIDEO_SKIPPED, {})
        view_totals = post_event_totals.get(EventType.VIDEO_VIEWED, {})
        feed_impressions = impression_totals.get("count", 0)
        unique_impression_viewers = impression_totals.get("unique_users", 0)
        video_skips = skip_totals.get("count", 0)
        video_views = view_totals.get("count", 0)

        def signal_cnt(*types: SignalType) -> int:
            return sum(int(signals.get(t, {}).get("count", 0)) for t in types)

        views = signal_cnt(SignalType.VIEW, SignalType.REWATCH)
        rewatches = signal_cnt(SignalType.REWATCH)
        likes = max(0, signal_cnt(SignalType.LIKE) - signal_cnt(SignalType.UNLIKE))
        unlikes = signal_cnt(SignalType.UNLIKE)
        saves = max(0, signal_cnt(SignalType.SAVE) - signal_cnt(SignalType.UNSAVE))
        unsaves = signal_cnt(SignalType.UNSAVE)
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
            "rewatches": rewatches,
            "total_not_interested": signal_cnt(SignalType.NOT_INTERESTED),
            "rewatch_rate": rewatches / views * 100 if views > 0 else None,
            "unique_viewers": unique_viewers["total"],
            "unique_viewer_rate": (
                unique_viewers["total"] / views * 100 if views > 0 else None
            ),
            "profile_views": profile_viewers["total"],
            "unique_profile_viewers": profile_viewers["unique_viewers"],
            "unique_profile_viewer_rate": (
                profile_viewers["unique_viewers"] / profile_viewers["total"] * 100
                if profile_viewers["total"] > 0
                else None
            ),
            "feed_impressions": feed_impressions,
            "unique_impression_viewers": unique_impression_viewers,
            "video_skips": video_skips,
            "video_skip_rate": video_skips / video_views * 100 if video_views > 0 else None,
            "likes": likes,
            "unlikes": unlikes,
            "like_rate": likes / views * 100 if views > 0 else None,
            "comments": total_comments,
            "comment_rate": total_comments / views * 100 if views > 0 else None,
            "shares": shares,
            "share_rate": shares / views * 100 if views > 0 else None,
            "saves": saves,
                        "unsaves": unsaves,
            "save_rate": saves / views * 100 if views > 0 else None,
            "new_followers": gained,
            "lost_followers": lost,
            "follower_growth": gained - lost,
            "completions": completions,
            "watch_sec": watch_sec,
            "total_watch_time": watch_sec,
            "engagements": engagements,
            "avg_watch_time": watch_sec / views if views > 0 else None,
            "completion_rate": completions / views * 100 if views > 0 else None,
            "engagement_rate": engagements / views * 100 if views > 0 else 0.0,
        }

    @staticmethod
    def _parse_timestamp(value) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @classmethod
    def _collaboration_timestamp(cls, collaboration, field: str) -> datetime | None:
        return cls._parse_timestamp(getattr(collaboration, field, None))

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
            request_at = self._collaboration_timestamp(collaboration, "created_at")
            request_at = self._collaboration_timestamp(collaboration, "proposed_at") or request_at
            if request_at is None:
                continue
            accepted_timestamps = [self._parse_timestamp(getattr(collaboration, "accepted_at", None))]
            accepted_timestamps.extend(
                self._parse_timestamp(getattr(participant, "accepted_at", None))
                for participant in getattr(collaboration, "participants", [])
            )
            accepted_timestamps = [timestamp for timestamp in accepted_timestamps if timestamp is not None]
            if accepted_timestamps:
                elapsed = (min(accepted_timestamps) - request_at).total_seconds() / 3600
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
        try:
            unique_commenters = await CommentRepository(
                self.db
            ).count_unique_commenters_for_creator(creator_id, start, end)
        except Exception:
            unique_commenters = 0
        unique_sharers = await self.analytics_repo.unique_signal_actor_count(
            creator_id=creator_id,
            signal_type=SignalType.SHARE,
            start=start,
            end=end,
        )
        unique_likers = await self.analytics_repo.unique_signal_actor_count(
            creator_id=creator_id,
            signal_type=SignalType.LIKE,
            start=start,
            end=end,
        )
        unique_savers = await self.analytics_repo.unique_signal_actor_count(
            creator_id=creator_id,
            signal_type=SignalType.SAVE,
            start=start,
            end=end,
        )
        unique_actor_counts = await self.analytics_repo.unique_signal_actor_counts(
            creator_id=creator_id,
            signal_types=[
                SignalType.UNLIKE,
                SignalType.UNSAVE,
                SignalType.REWATCH,
                SignalType.COMPLETION,
                SignalType.FOLLOW,
                SignalType.NOT_INTERESTED,
            ],
            start=start,
            end=end,
        )
        unique_viewers = current["unique_viewers"]
        collaborations = await self._collaboration_totals(creator_id, start, end)
        from repositories.social_repository import FollowRepository

        current_followers = await FollowRepository(self.db).count_followers(creator_id)

        top_video_perf = await self.video_performance(creator_id, start, end)
        top_video_perf.sort(key=lambda item: item["engagement_rate"], reverse=True)

        return {
            "total_posts": len(posts),
            "total_uploads": len(posts),
            "total_published_videos": len(posts),
            "total_views": current["views"],
            "total_rewatches": current["rewatches"],
            "total_not_interested": current["total_not_interested"],
            "rewatch_rate": current["rewatch_rate"],
            "unique_viewers": current["unique_viewers"],
            "unique_viewer_rate": current["unique_viewer_rate"],
            "profile_views": current["profile_views"],
            "unique_profile_viewers": current["unique_profile_viewers"],
            "unique_profile_viewer_rate": current["unique_profile_viewer_rate"],
            "feed_impressions": current["feed_impressions"],
            "unique_impression_viewers": current["unique_impression_viewers"],
            "profile_views_growth_pct": self._growth_pct(
                current["profile_views"], previous["profile_views"]
            ),
            "feed_impressions_growth_pct": self._growth_pct(
                current["feed_impressions"], previous["feed_impressions"]
            ),
            "video_skips": current["video_skips"],
            "video_skip_rate": current["video_skip_rate"],
            "total_likes": current["likes"],
            "total_unlikes": current["unlikes"],
            "unique_likers": unique_likers,
            "unique_unlikers": unique_actor_counts.get(SignalType.UNLIKE, 0),
            "like_rate": current["like_rate"],
            "total_comments": current["comments"],
            "unique_commenters": unique_commenters,
            "comment_rate": current["comment_rate"],
            "total_shares": current["shares"],
            "unique_sharers": unique_sharers,
            "share_rate": current["share_rate"],
            "total_saves": current["saves"],
            "total_unsaves": current["unsaves"],
            "unique_savers": unique_savers,
            "unique_unsavers": unique_actor_counts.get(SignalType.UNSAVE, 0),
            "save_rate": current["save_rate"],
            "new_followers": current["new_followers"],
            "lost_followers": current["lost_followers"],
            "follower_growth": current["follower_growth"],
            "current_followers": current_followers,
            "total_watch_time": current["total_watch_time"],
            "avg_watch_time": current["avg_watch_time"],
            "total_completions": current["completions"],
            "completion_rate": current["completion_rate"],
            "unique_completers": unique_actor_counts.get(SignalType.COMPLETION, 0),
            "unique_completion_rate": (
                unique_actor_counts.get(SignalType.COMPLETION, 0) / unique_viewers * 100
                if unique_viewers > 0
                else None
            ),
            "unique_rewatchers": unique_actor_counts.get(SignalType.REWATCH, 0),
            "unique_rewatch_rate": (
                unique_actor_counts.get(SignalType.REWATCH, 0) / unique_viewers * 100
                if unique_viewers > 0
                else None
            ),
            "unique_new_followers": unique_actor_counts.get(SignalType.FOLLOW, 0),
            "unique_not_interested_users": unique_actor_counts.get(
                SignalType.NOT_INTERESTED, 0
            ),
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
        unique_viewers = await self.analytics_repo.unique_viewer_counts(
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
                "unique_viewers": unique_viewers["by_post"].get(post.id, 0),
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
            created_at = self._collaboration_timestamp(collaboration, "created_at")
            if created_at is None:
                continue
            date_key = created_at.date().isoformat()
            point = by_date.setdefault(date_key, {})
            point["collaborations_requested"] = point.get("collaborations_requested", 0) + 1
            point["collaborations_pending"] = point.get("collaborations_pending", 0) + int(
                collaboration.status == CollaborationStatus.PROPOSED
            )
            point["collaborations_accepted"] = point.get("collaborations_accepted", 0) + int(
                collaboration.status in accepted_statuses
            )
            point["collaborations_in_progress"] = point.get("collaborations_in_progress", 0) + int(
                collaboration.status == CollaborationStatus.IN_PROGRESS
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
                "collaborations_in_progress": 0,
                "collaborations_declined": 0,
                "collaborations_completed": 0,
                "collaborations_cancelled": 0,
                **by_date.get(date_key, {}),
            })
            current_day += timedelta(days=1)
        return points