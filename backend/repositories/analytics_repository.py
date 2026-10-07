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
from typing import TypedDict

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import InteractionSignal, SignalType
from app.models.streaming import StreamSession
from repositories.base import BaseRepository
from repositories.feed_ranking import ViewerPostSignals


class UniqueViewerCounts(TypedDict):
    total: int
    by_post: dict[uuid.UUID, int]


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
        stream_session_id: uuid.UUID | None = None,
        value: float = 1.0,
        paid_delivery_id: uuid.UUID | None = None,
    ) -> InteractionSignal:
        if stream_session_id is not None and (
            signal_type != SignalType.FOLLOW or post_id is not None
        ):
            raise ValueError("Only creator follow signals can be attributed to a stream session")
        validated_paid_delivery_id = None
        paid_campaign_id = None
        if paid_delivery_id is not None:
            if post_id is None:
                raise ValueError("Paid engagement requires a post")
            from repositories.paid_delivery_repository import PaidDeliveryRepository

            delivery = await PaidDeliveryRepository(self.db).impressed_for_engagement(
                paid_delivery_id, user_id, post_id
            )
            validated_paid_delivery_id = delivery.id
            paid_campaign_id = delivery.campaign_id
        signal = InteractionSignal(
            user_id=user_id,
            post_id=post_id,
            creator_id=creator_id,
            stream_session_id=stream_session_id,
            paid_delivery_id=validated_paid_delivery_id,
            paid_campaign_id=paid_campaign_id,
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
        paid_campaign_id: uuid.UUID | None = None,
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
        if paid_campaign_id is not None:
            stmt = stmt.where(InteractionSignal.paid_campaign_id == paid_campaign_id)
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
        paid_campaign_id: uuid.UUID | None = None,
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
        if paid_campaign_id is not None:
            stmt = stmt.where(InteractionSignal.paid_campaign_id == paid_campaign_id)
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

    async def unique_viewer_counts(
        self,
        *,
        creator_id: uuid.UUID,
        post_ids: list[uuid.UUID] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> UniqueViewerCounts:
        """Return distinct viewers across a creator and optionally per post.

        Both first views and rewatches count as viewing activity, while each
        user is counted once per scope.
        """
        filters = [
            InteractionSignal.creator_id == creator_id,
            InteractionSignal.signal_type.in_([SignalType.VIEW, SignalType.REWATCH]),
        ]
        if start is not None:
            filters.append(InteractionSignal.created_at >= start)
        if end is not None:
            filters.append(InteractionSignal.created_at <= end)

        total_stmt = select(
            literal(None, type_=InteractionSignal.post_id.type).label("post_id"),
            func.count(func.distinct(InteractionSignal.user_id)).label("unique_viewers"),
        ).where(*filters)

        stmt = total_stmt
        if post_ids:
            post_stmt = (
                select(
                    InteractionSignal.post_id,
                    func.count(func.distinct(InteractionSignal.user_id)).label("unique_viewers"),
                )
                .where(*filters, InteractionSignal.post_id.in_(post_ids))
                .group_by(InteractionSignal.post_id)
            )
            stmt = union_all(total_stmt, post_stmt)

        result = await self.db.execute(stmt)
        total = 0
        by_post: dict[uuid.UUID, int] = {}
        for row in result.all():
            if row.post_id is None:
                total = int(row.unique_viewers or 0)
            else:
                by_post[row.post_id] = int(row.unique_viewers or 0)
        return {"total": total, "by_post": by_post}

    async def unique_signal_actor_count(
        self,
        *,
        creator_id: uuid.UUID,
        signal_type: SignalType,
        start: datetime,
        end: datetime,
    ) -> int:
        """Count distinct users producing one signal type for a creator in a period."""
        stmt = select(func.count(func.distinct(InteractionSignal.user_id))).where(
            InteractionSignal.creator_id == creator_id,
            InteractionSignal.signal_type == signal_type,
            InteractionSignal.created_at >= start,
            InteractionSignal.created_at <= end,
        )
        result = await self.db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def stream_attributed_follow_count(
        self, *, creator_id: uuid.UUID, start: datetime, end: datetime
    ) -> int:
        """Count follow events attributed to this creator's own streams in [start, end)."""
        totals = await self.creator_stream_follow_totals(
            creator_id=creator_id, start=start, end=end
        )
        return totals["stream_attributed_follows"]

    async def creator_stream_follow_totals(
        self, *, creator_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Count persisted transitions and distinct actors across a creator's streams."""
        if end <= start:
            raise ValueError("Reporting end must be after reporting start")
        stmt = (
            select(
                func.count().label("follows"),
                func.count(func.distinct(InteractionSignal.user_id)).label("followers"),
            )
            .select_from(InteractionSignal)
            .join(StreamSession, StreamSession.id == InteractionSignal.stream_session_id)
            .where(
                InteractionSignal.creator_id == creator_id,
                InteractionSignal.signal_type == SignalType.FOLLOW,
                InteractionSignal.post_id.is_(None),
                InteractionSignal.stream_session_id.is_not(None),
                StreamSession.owner_id == creator_id,
                InteractionSignal.created_at >= start,
                InteractionSignal.created_at < end,
            )
        )
        result = await self.db.execute(stmt)
        row = result.one()
        return {
            "stream_attributed_follows": int(row.follows or 0),
            "unique_stream_followers": int(row.followers or 0),
        }

    async def unique_signal_actor_counts(
        self,
        *,
        creator_id: uuid.UUID,
        signal_types: list[SignalType],
        start: datetime,
        end: datetime,
    ) -> dict[SignalType, int]:
        """Count distinct actors grouped by signal type for one creator period."""
        if not signal_types:
            return {}

        stmt = (
            select(
                InteractionSignal.signal_type,
                func.count(func.distinct(InteractionSignal.user_id)).label("actors"),
            )
            .where(
                InteractionSignal.creator_id == creator_id,
                InteractionSignal.signal_type.in_(signal_types),
                InteractionSignal.created_at >= start,
                InteractionSignal.created_at <= end,
            )
            .group_by(InteractionSignal.signal_type)
        )
        result = await self.db.execute(stmt)
        return {row.signal_type: int(row.actors or 0) for row in result.all()}

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
            select(
                InteractionSignal.creator_id,
                InteractionSignal.signal_type,
                func.sum(InteractionSignal.value).label("total"),
            )
            .where(
                InteractionSignal.user_id == user_id,
                InteractionSignal.paid_delivery_id.is_(None),
            )
            .group_by(InteractionSignal.creator_id, InteractionSignal.signal_type)
        )
        scores: dict[uuid.UUID, float] = {}
        negative_types = {SignalType.UNLIKE, SignalType.UNSAVE, SignalType.UNFOLLOW}
        for creator_id, signal_type, total in result.all():
            if signal_type == SignalType.NOT_INTERESTED:
                continue
            multiplier = -1.0 if signal_type in negative_types else 1.0
            scores[creator_id] = scores.get(creator_id, 0.0) + multiplier * float(total or 0.0)

        return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]

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
            .where(
                InteractionSignal.user_id == user_id,
                InteractionSignal.post_id.in_(post_ids),
                InteractionSignal.paid_delivery_id.is_(None),
            )
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
            .where(
                InteractionSignal.post_id.in_(post_ids),
                InteractionSignal.paid_delivery_id.is_(None),
            )
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

    async def recent_post_engagement(
        self, post_ids: list[uuid.UUID], since: datetime
    ) -> dict[uuid.UUID, dict[str, float]]:
        """Aggregate post interaction events since a cutoff for Viral ranking."""
        if not post_ids:
            return {}

        stmt = (
            select(
                InteractionSignal.post_id,
                InteractionSignal.signal_type,
                func.count().label("cnt"),
            )
            .where(
                InteractionSignal.post_id.in_(post_ids),
                InteractionSignal.created_at >= since,
                InteractionSignal.paid_delivery_id.is_(None),
            )
            .group_by(InteractionSignal.post_id, InteractionSignal.signal_type)
        )
        result = await self.db.execute(stmt)

        out: dict[uuid.UUID, dict[str, float]] = {}
        signal_names = {
            SignalType.VIEW: "views",
            SignalType.COMPLETION: "completions",
            SignalType.REWATCH: "rewatches",
            SignalType.LIKE: "likes",
            SignalType.UNLIKE: "unlikes",
            SignalType.SAVE: "saves",
            SignalType.UNSAVE: "unsaves",
            SignalType.SHARE: "shares",
        }
        for post_id, signal_type, count in result.all():
            name = signal_names.get(signal_type)
            if name is not None:
                out.setdefault(post_id, {})[name] = float(count or 0)
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
                InteractionSignal.paid_delivery_id.is_(None),
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
