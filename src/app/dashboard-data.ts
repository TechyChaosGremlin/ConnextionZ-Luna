// ─── DASHBOARD DATA ──────────────────────────────────────────────────────────
//
// Dashboard analytics are loaded from the authenticated creator GraphQL queries.
// The screen owns presentation only; aggregation and date filtering stay in the backend.

import { type Result } from "./auth-store";
import { analyticsPeriod } from "./analytics-utils";
import { type ContentItem } from "./creators";
import { graphqlRequestResult } from "./profile-graphql";

// ─── SHAPES ──────────────────────────────────────────────────────────────────

export type Range = "7d" | "30d" | "90d";

export const RANGES: { id: Range; label: string; days: number }[] = [
  { id: "7d", label: "7 days", days: 7 },
  { id: "30d", label: "30 days", days: 30 },
  { id: "90d", label: "90 days", days: 90 },
];

export type MetricKey = "views" | "likes" | "comments" | "shares" | "followers";

export interface Metric {
  key: MetricKey;
  label: string;
  /** Total across the range. */
  value: number;
  /** Change against the previous range of the same length, as a percentage. */
  deltaPct: number;
  /** One point per day, oldest first — what the sparkline and bars draw. */
  series: number[];
}

export interface CollabStats {
  totalRequests: number | null;
  pending: number | null;
  accepted: number | null;
  declined: number | null;
  cancelled: number | null;
  completed: number | null;
  active: number | null;
  acceptanceRatePct: number | null;
  completionRatePct: number | null;
  avgResponseHours: number | null;
  trends: CollaborationTrend[];
}

export interface CollaborationTrend {
  date: string;
  requested: number;
  pending: number;
  accepted: number;
  active: number;
  declined: number;
  completed: number;
  cancelled: number;
}

/** A row in the content-management list. */
export interface ContentRow extends ContentItem {
  comments: number;
  shares: number;
  createdAt?: number;
}

export interface DashboardData {
  range: Range;
  days: number;
  metrics: Metric[];
  collab: CollabStats;
  content: ContentRow[];
  /** Best-performing post in the range — the "what worked" callout. */
  best?: ContentRow;
  quality: {
    uniqueViewers: number;
    saves: number;
    avgWatchTime: number | null;
    completionRate: number | null;
    engagementRate: number;
  };
  generatedAt: number;
}

const METRIC_LABELS: Record<MetricKey, string> = {
  views: "Views",
  likes: "Likes",
  comments: "Comments",
  shares: "Shares",
  followers: "New followers",
};

export interface CreatorAnalyticsResponse {
  creatorAnalytics: {
    totalViews: number; uniqueViewers: number; totalLikes: number; totalComments: number;
    totalShares: number; totalSaves: number; followerGrowth: number; newFollowers: number;
    lostFollowers: number; avgWatchTime: number | null; completionRate: number | null;
    engagementRate: number; totalPosts: number; activeCollaborations: number; completedCollaborations: number;
    totalCollaborationRequests: number; pendingCollaborations: number; acceptedCollaborations: number;
    declinedCollaborations: number; cancelledCollaborations: number; collaborationAcceptanceRate: number | null;
    collaborationCompletionRate: number | null; averageResponseHours: number | null;
    viewsGrowthPct: number | null; likesGrowthPct: number | null;
    commentsGrowthPct: number | null; sharesGrowthPct: number | null; followersGrowthPct: number | null;
  };
  creatorVideoAnalytics: {
    post: {
      id: string; caption: string | null; viewCount: number; likeCount: number;
      commentCount: number; shareCount: number; status: string; scheduledAt: string | null;
      createdAt: string; media: { thumbnailUrl: string | null; url: string }[];
    };
    views: number; likes: number; comments: number; shares: number; saves: number;
  }[];
  creatorAnalyticsTrends: {
    date: string; views: number; likes: number; comments: number; shares: number;
    saves: number; followersGained: number; collaborationsRequested: number; collaborationsPending: number;
    collaborationsAccepted: number; collaborationsInProgress: number; collaborationsDeclined: number; collaborationsCompleted: number; collaborationsCancelled: number;
  }[];
}

export async function fetchCreatorAnalytics(range: Range): Promise<Result<CreatorAnalyticsResponse>> {
  return graphqlRequestResult<CreatorAnalyticsResponse>(`
    query CreatorAnalytics($period: AnalyticsPeriod!) {
      creatorAnalytics(period: $period) {
        totalPosts totalViews uniqueViewers totalLikes totalComments totalShares totalSaves
        followerGrowth newFollowers lostFollowers avgWatchTime completionRate engagementRate
        activeCollaborations completedCollaborations
        totalCollaborationRequests pendingCollaborations acceptedCollaborations declinedCollaborations cancelledCollaborations
        collaborationAcceptanceRate collaborationCompletionRate averageResponseHours
        viewsGrowthPct likesGrowthPct commentsGrowthPct sharesGrowthPct followersGrowthPct
      }
      creatorVideoAnalytics(period: $period, sortBy: "views") {
        post { id caption viewCount likeCount commentCount shareCount status scheduledAt createdAt media { thumbnailUrl url } }
        views likes comments shares saves
      }
      creatorAnalyticsTrends(period: $period) { date views likes comments shares saves followersGained }
    }
  `, { period: analyticsPeriod(range) });
}

// ─── FETCH ───────────────────────────────────────────────────────────────────

/**
 * The network seam. Latency is real enough that the screen's skeleton is worth
 * having, and an offline browser fails the way a fetch would — the dashboard is
 * the screen where a silent stale number would be most misleading.
 */
export async function fetchDashboard(range: Range): Promise<Result<DashboardData>> {
  const days = RANGES.find((r) => r.id === range)!.days;
  const result = await fetchCreatorAnalytics(range);
  if (!result.ok) return result;

  const summary = result.value.creatorAnalytics;
  const end = new Date();
  const start = new Date(end.getTime() - (days - 1) * 86_400_000);
  const trendsByDate = new Map(result.value.creatorAnalyticsTrends.map((point) => [point.date.slice(0, 10), point]));
  const dates = Array.from({ length: days }, (_, index) => {
    const date = new Date(start.getTime() + index * 86_400_000);
    return date.toISOString().slice(0, 10);
  });
  const seriesFor = (key: "views" | "likes" | "comments" | "shares" | "followers") =>
    dates.map((date) => {
      const point = trendsByDate.get(date);
      return point ? (key === "followers" ? point.followersGained : point[key]) : 0;
    });
  const metric = (key: MetricKey, value: number, deltaPct: number | null): Metric => ({
    key, label: METRIC_LABELS[key], value, deltaPct: deltaPct ?? 0, series: seriesFor(key),
  });
  const content = result.value.creatorVideoAnalytics.map((row) => ({
    id: row.post.id,
    thumbnail: row.post.media[0]?.thumbnailUrl ?? row.post.media[0]?.url ?? "",
    caption: row.post.caption ?? "",
    views: row.views,
    likes: row.likes,
    comments: row.comments,
    shares: row.shares,
    status: row.post.status.toLowerCase() as ContentItem["status"],
    scheduledAt: row.post.scheduledAt ?? undefined,
    createdAt: Date.parse(row.post.createdAt),
  } as ContentRow));
  const collab: CollabStats = {
    totalRequests: summary.totalCollaborationRequests,
    pending: summary.pendingCollaborations,
    accepted: summary.acceptedCollaborations,
    declined: summary.declinedCollaborations,
    cancelled: summary.cancelledCollaborations,
    completed: summary.completedCollaborations,
    active: summary.activeCollaborations,
    acceptanceRatePct: summary.collaborationAcceptanceRate,
    completionRatePct: summary.collaborationCompletionRate,
    avgResponseHours: summary.averageResponseHours,
    trends: result.value.creatorAnalyticsTrends.map((point) => ({
      date: point.date,
      requested: point.collaborationsRequested,
      pending: point.collaborationsPending,
      accepted: point.collaborationsAccepted,
      active: point.collaborationsInProgress,
      declined: point.collaborationsDeclined,
      completed: point.collaborationsCompleted,
      cancelled: point.collaborationsCancelled,
    })),
  };
  return {
    ok: true,
    value: {
      range, days,
      metrics: [
        metric("views", summary.totalViews, summary.viewsGrowthPct),
        metric("likes", summary.totalLikes, summary.likesGrowthPct),
        metric("comments", summary.totalComments, summary.commentsGrowthPct),
        metric("shares", summary.totalShares, summary.sharesGrowthPct),
        metric("followers", summary.newFollowers, summary.followersGrowthPct),
      ],
      collab, content, best: content[0], quality: {
        uniqueViewers: summary.uniqueViewers,
        saves: summary.totalSaves,
        avgWatchTime: summary.avgWatchTime,
        completionRate: summary.completionRate,
        engagementRate: summary.engagementRate,
      }, generatedAt: Date.now(),
    },
  };
}

// ─── FORMATTING ──────────────────────────────────────────────────────────────

/** Axis labels: "Mon", or a date once the window is longer than a fortnight. */
export function axisLabels(days: number, now = Date.now()): string[] {
  return Array.from({ length: days }, (_, i) => {
    const date = new Date(now - (days - 1 - i) * 86_400_000);
    return days <= 14
      ? date.toLocaleDateString(undefined, { weekday: "short" })
      : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  });
}

export const signed = (n: number) => `${n > 0 ? "+" : ""}${n}%`;
