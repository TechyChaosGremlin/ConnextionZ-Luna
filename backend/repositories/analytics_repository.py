"""Unified recommendation-ready engagement signal log.

See ``app.models.analytics.InteractionSignal`` for the rationale — this
repository is the single write/read path for that append-only event
stream, kept separate from the toggle-state repositories in
``social_repository`` so feature-extraction queries don't have to touch
five different tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import InteractionSignal, SignalType
from repositories.base import BaseRepository


class AnalyticsRepository(BaseRepository[InteractionSignal]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, InteractionSignal)

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        creator_id: uuid.UUID,
        signal_type: SignalType,
        post_id: uuid.UUID | None = None,
        value: float = 1.0,
    ) -> InteractionSignal:
        signal = InteractionSignal(
            user_id=user_id,
            post_id=post_id,
            creator_id=creator_id,
            signal_type=signal_type,
            value=value,
        )
        self.db.add(signal)
        await self.db.flush()
        return signal

    async def get_for_post(self, post_id: uuid.UUID, limit: int = 500) -> list[InteractionSignal]:
        result = await self.db.execute(
            select(InteractionSignal)
            .where(InteractionSignal.post_id == post_id)
            .order_by(InteractionSignal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_for_user(self, user_id: uuid.UUID, limit: int = 500) -> list[InteractionSignal]:
        result = await self.db.execute(
            select(InteractionSignal)
            .where(InteractionSignal.user_id == user_id)
            .order_by(InteractionSignal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def signal_totals(
        self,
        *,
        creator_id: uuid.UUID | None = None,
        post_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[SignalType, dict[str, float]]:
        """Per-signal-type event count + summed value, scoped to a creator and/or
        post and/or date range — powers the creator/post analytics dashboard."""
        stmt = select(
            InteractionSignal.signal_type,
            func.count().label("cnt"),
            func.sum(InteractionSignal.value).label("total"),
        ).group_by(InteractionSignal.signal_type)
        if creator_id is not None:
            stmt = stmt.where(InteractionSignal.creator_id == creator_id)
        if post_id is not None:
            stmt = stmt.where(InteractionSignal.post_id == post_id)
        if start is not None:
            stmt = stmt.where(InteractionSignal.created_at >= start)
        if end is not None:
            stmt = stmt.where(InteractionSignal.created_at <= end)
        result = await self.db.execute(stmt)
        return {
            row.signal_type: {"count": row.cnt, "total": float(row.total or 0.0)}
            for row in result.all()
        }

    async def per_post_signal_totals(
        self,
        *,
        creator_id: uuid.UUID | None = None,
        post_ids: list[uuid.UUID] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[uuid.UUID, dict[SignalType, dict[str, float]]]:
        """Per-post and per-signal-type totals in a single grouped query."""
        stmt = (
            select(
                InteractionSignal.post_id,
                InteractionSignal.signal_type,
                func.count().label("cnt"),
                func.sum(InteractionSignal.value).label("total"),
            )
            .where(InteractionSignal.post_id.is_not(None))
            .group_by(InteractionSignal.post_id, InteractionSignal.signal_type)
        )
        if creator_id is not None:
            stmt = stmt.where(InteractionSignal.creator_id == creator_id)
        if post_ids:
            stmt = stmt.where(InteractionSignal.post_id.in_(post_ids))
        if start is not None:
            stmt = stmt.where(InteractionSignal.created_at >= start)
        if end is not None:
            stmt = stmt.where(InteractionSignal.created_at <= end)

        result = await self.db.execute(stmt)
        totals: dict[uuid.UUID, dict[SignalType, dict[str, float]]] = {}
        for row in result.all():
            p_id = row.post_id
            if p_id not in totals:
                totals[p_id] = {}
            totals[p_id][row.signal_type] = {
                "count": row.cnt,
                "total": float(row.total or 0.0),
            }
        return totals

    async def daily_signal_totals(
        self,
        *,
        creator_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """Daily aggregated activity points grouped by UTC date."""
        day = func.date(InteractionSignal.created_at).label("day")
        stmt = (
            select(
                day,
                InteractionSignal.signal_type,
                func.count().label("cnt"),
                func.sum(InteractionSignal.value).label("total"),
            )
            .group_by(day, InteractionSignal.signal_type)
            .order_by(day.asc())
        )
        if creator_id is not None:
            stmt = stmt.where(InteractionSignal.creator_id == creator_id)
        if start is not None:
            stmt = stmt.where(InteractionSignal.created_at >= start)
        if end is not None:
            stmt = stmt.where(InteractionSignal.created_at <= end)

        result = await self.db.execute(stmt)
        by_date: dict[str, dict] = {}
        for row in result.all():
            date_str = str(row.day)
            if date_str not in by_date:
                by_date[date_str] = {
                    "views": 0,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "saves": 0,
                    "followers_gained": 0,
                    "followers_lost": 0,
                }
            st = row.signal_type
            cnt = int(row.cnt or 0)
            if st in (SignalType.VIEW, SignalType.REWATCH):
                by_date[date_str]["views"] += cnt
            elif st == SignalType.LIKE:
                by_date[date_str]["likes"] += cnt
            elif st == SignalType.UNLIKE:
                by_date[date_str]["likes"] = max(0, by_date[date_str]["likes"] - cnt)
            elif st == SignalType.SAVE:
                by_date[date_str]["saves"] += cnt
            elif st == SignalType.UNSAVE:
                by_date[date_str]["saves"] = max(0, by_date[date_str]["saves"] - cnt)
            elif st == SignalType.SHARE:
                by_date[date_str]["shares"] += cnt
            elif st == SignalType.FOLLOW:
                by_date[date_str]["followers_gained"] += cnt
            elif st == SignalType.UNFOLLOW:
                by_date[date_str]["followers_lost"] += cnt

        return [
            {"date": d, **v, "net_followers": v["followers_gained"] - v["followers_lost"]}
            for d, v in sorted(by_date.items())
        ]

    async def creator_affinity(self, user_id: uuid.UUID, limit: int = 20) -> list[tuple[uuid.UUID, float]]:
        """Creators this user engages with most, weighted by signal value."""
        result = await self.db.execute(
            select(InteractionSignal.creator_id, func.sum(InteractionSignal.value).label("score"))
            .where(InteractionSignal.user_id == user_id)
            .group_by(InteractionSignal.creator_id)
            .order_by(func.sum(InteractionSignal.value).desc())
            .limit(limit)
        )
        return [(row[0], float(row[1])) for row in result.all()]

    async def viewer_post_history(
        self, user_id: uuid.UUID, post_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, "ViewerPostSignals"]:
        """The viewer's past behavior on a candidate set of posts, for ranking.

        One grouped query over the unified signal log, aggregated in Python
        into ``ViewerPostSignals`` per post: the single best watch event
        (repeat ``post_watches`` rows are collapsed so one post contributes
        at most its max watch to ranking), whether any completion fired, and
        whether the viewer has a net like/save or any share. Posts with no
        signals are simply absent from the returned dict.
        """
        from repositories.feed_ranking import ViewerPostSignals

        if not post_ids:
            return {}

        stmt = (
            select(
                InteractionSignal.post_id,
                InteractionSignal.signal_type,
                func.count().label("cnt"),
                func.max(InteractionSignal.value).label("max_value"),
            )
            .where(InteractionSignal.user_id == user_id)
            .where(InteractionSignal.post_id.in_(post_ids))
            .group_by(InteractionSignal.post_id, InteractionSignal.signal_type)
        )
        result = await self.db.execute(stmt)

        watched: dict[uuid.UUID, float] = {}
        completed: set[uuid.UUID] = set()
        net_likes: dict[uuid.UUID, int] = {}
        net_saves: dict[uuid.UUID, int] = {}
        shared: set[uuid.UUID] = set()
        not_interested: set[uuid.UUID] = set()
        for post_id, signal_type, cnt, max_value in result.all():
            if signal_type == SignalType.WATCH_DURATION:
                watched[post_id] = max(watched.get(post_id, 0.0), float(max_value or 0.0))
            elif signal_type == SignalType.COMPLETION:
                completed.add(post_id)
            elif signal_type == SignalType.LIKE:
                net_likes[post_id] = net_likes.get(post_id, 0) + cnt
            elif signal_type == SignalType.UNLIKE:
                net_likes[post_id] = net_likes.get(post_id, 0) - cnt
            elif signal_type == SignalType.SAVE:
                net_saves[post_id] = net_saves.get(post_id, 0) + cnt
            elif signal_type == SignalType.UNSAVE:
                net_saves[post_id] = net_saves.get(post_id, 0) - cnt
            elif signal_type == SignalType.SHARE:
                shared.add(post_id)
            elif signal_type == SignalType.NOT_INTERESTED:
                not_interested.add(post_id)

        post_ids_with_signals = (
            set(watched) | completed | set(net_likes) | set(net_saves) | shared | not_interested
        )
        return {
            post_id: ViewerPostSignals(
                watched_seconds=watched.get(post_id, 0.0),
                completed=post_id in completed,
                engaged=(
                    net_likes.get(post_id, 0) > 0
                    or net_saves.get(post_id, 0) > 0
                    or post_id in shared
                ),
                not_interested=post_id in not_interested,
            )
            for post_id in post_ids_with_signals
        }

    async def post_engagement_rates(
        self, post_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, float]]:
        """Aggregate production watch/completion/rewatch counts per post.

        One grouped query over the unified ``InteractionSignal`` log for the
        candidate pool — feeds ``feed_ranking.build_engagement`` which turns
        these raw counts into bounded, normalized rates. Posts with no signals
        (fresh content) are simply absent from the returned dict, so scoring
        treats them as having zero watch history rather than fabricating any.
        """
        if not post_ids:
            return {}

        stmt = (
            select(
                InteractionSignal.post_id,
                InteractionSignal.signal_type,
                func.count().label("cnt"),
                func.sum(InteractionSignal.value).label("total"),
            )
            .where(InteractionSignal.post_id.in_(post_ids))
            .group_by(InteractionSignal.post_id, InteractionSignal.signal_type)
        )
        result = await self.db.execute(stmt)

        out: dict[uuid.UUID, dict[str, float]] = {}
        for post_id, signal_type, cnt, total in result.all():
            bucket = out.setdefault(
                post_id, {"views": 0.0, "watch_seconds": 0.0, "completions": 0.0, "rewatches": 0.0}
            )
            if signal_type == SignalType.VIEW:
                bucket["views"] += float(cnt)
            elif signal_type == SignalType.WATCH_DURATION:
                bucket["watch_seconds"] += float(total or 0.0)
            elif signal_type == SignalType.COMPLETION:
                bucket["completions"] += float(cnt)
            elif signal_type == SignalType.REWATCH:
                bucket["rewatches"] += float(cnt)
        return out

    async def user_interest_tags(self, user_id: uuid.UUID, limit: int = 20) -> list[str]:
        """The viewer's demonstrated interest topics.

        Counts tags on posts the viewer actively engaged with (liked, saved,
        shared, completed, or rewatched) via the production signal log. Used to
        source the interest-matching candidate pool. Bounded query (at most 200
        engaged posts inspected) and a bounded returned vocabulary.
        """
        from collections import Counter

        from app.models.content import Post

        active = [
            SignalType.LIKE,
            SignalType.SAVE,
            SignalType.SHARE,
            SignalType.COMPLETION,
            SignalType.REWATCH,
        ]
        stmt = (
            select(Post.tags)
            .join(InteractionSignal, InteractionSignal.post_id == Post.id)
            .where(
                InteractionSignal.user_id == user_id,
                InteractionSignal.post_id.is_not(None),
                InteractionSignal.signal_type.in_(active),
                Post.tags.is_not(None),
            )
            .limit(200)
        )
        result = await self.db.execute(stmt)

        counts: Counter = Counter()
        for (tags,) in result.all():
            for tag in tags or []:
                if isinstance(tag, str) and tag:
                    counts[tag] += 1
        return [tag for tag, _ in counts.most_common(limit)]
