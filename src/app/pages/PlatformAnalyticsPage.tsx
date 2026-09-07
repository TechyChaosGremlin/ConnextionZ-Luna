import { useEffect, useState } from "react";
import { ArrowLeft, RefreshCw, ShieldAlert } from "lucide-react";
import { graphqlRequestResult } from "../profile-graphql";
import { ACCENT, useTokens } from "../settings-ui";
import { useTheme } from "../ThemeContext";
import type { PageProps } from "./settingsPages.types";

type Range = "today" | "7d" | "28d" | "90d";
const ranges: { id: Range; label: string; days: number }[] = [
  { id: "today", label: "Today", days: 1 },
  { id: "7d", label: "7 Days", days: 7 },
  { id: "28d", label: "28 Days", days: 28 },
  { id: "90d", label: "90 Days", days: 90 },
];
type TopSort = "views" | "likes" | "comments" | "shares" | "saves" | "engagement" | "completion_rate";
const topSorts: { id: TopSort; label: string }[] = [
  { id: "views", label: "Views" },
  { id: "engagement", label: "Engagement" },
  { id: "completion_rate", label: "Completion rate" },
  { id: "likes", label: "Likes" },
  { id: "comments", label: "Comments" },
  { id: "shares", label: "Shares" },
  { id: "saves", label: "Saves" },
];
type TrendKey = "views" | "engagement" | "uploads" | "publishedVideos";

type PlatformData = {
  platformAnalytics: {
    totalUsers: number; newUsers: number; totalCreators: number; newCreators: number;
    activeUsers: number; activeCreators: number; dailyActiveUsers: number | null; weeklyActiveUsers: number | null; monthlyActiveUsers: number | null; totalViews: number;
    totalPublishedVideos: number; engagementRate: number; followsCreated: number;
    collabsCreated: number; totalLikes: number; totalComments: number; totalShares: number;
    totalSaves: number; averageWatchTime: number | null; completionRate: number | null;
    approvedContent: number; flaggedContent: number; removedContent: number;
    comparison: { userGrowthPct: number | null; creatorGrowthPct: number | null; contentGrowthPct: number | null; viewsGrowthPct: number | null; engagementGrowthPct: number | null; activeUsersGrowthPct: number | null } | null;
  };
  platformAnalyticsTrends: { date: string; views: number; uploads: number; publishedVideos: number; engagement: number }[];
  platformTopContent: { post: { id: string; caption: string; thumbnail: string }; views: number; likes: number; comments: number; shares: number; saves: number; engagementRate: number; completionRate: number | null }[];
};

export function PlatformAnalyticsPage({ onBack, t }: PageProps) {
  const isDark = useTheme();
  const tokens = useTokens(isDark);
  const [range, setRange] = useState<Range>("28d");
  const [sortBy, setSortBy] = useState<TopSort>("views");
  const [trendKey, setTrendKey] = useState<TrendKey>("views");
  const [data, setData] = useState<PlatformData | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const load = async (next: Range) => {
    setStatus("loading");
    const option = ranges.find((item) => item.id === next)!;
    const end = new Date();
    const start = new Date(end.getTime() - option.days * 86_400_000);
    const result = await graphqlRequestResult<PlatformData>(`
      query PlatformAnalytics($period: AnalyticsPeriod!, $sortBy: String!) {
        platformAnalytics(period: $period) { totalUsers newUsers totalCreators newCreators activeUsers activeCreators dailyActiveUsers weeklyActiveUsers monthlyActiveUsers totalViews totalPublishedVideos engagementRate followsCreated collabsCreated totalLikes totalComments totalShares totalSaves averageWatchTime completionRate approvedContent flaggedContent removedContent comparison { userGrowthPct creatorGrowthPct contentGrowthPct viewsGrowthPct engagementGrowthPct activeUsersGrowthPct } }
        platformAnalyticsTrends(period: $period) { date views uploads publishedVideos engagement }
        platformTopContent(period: $period, sortBy: $sortBy) { post { id caption thumbnail } views likes comments shares saves engagementRate completionRate }
      }
    `, { period: { start: start.toISOString(), end: end.toISOString() }, sortBy });
    if (!result.ok) { setStatus("error"); return; }
    setData(result.value);
    setStatus("ready");
  };

  useEffect(() => { void load(range); }, [range, sortBy]);

  return (
    <div className="absolute inset-0 z-30 flex flex-col" style={{ background: tokens.bg }}>
      <header className="flex items-center gap-3 px-5 pt-14 pb-4" style={{ borderBottom: `1px solid ${tokens.divider}` }}>
        <button onClick={onBack} aria-label="Back" className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: tokens.backBtnBg, border: tokens.cardBorder }}><ArrowLeft className="w-4 h-4" /></button>
        <div className="flex-1"><h1 className="font-extrabold text-[22px]" style={{ color: tokens.heading }}>Platform Analytics</h1><p className="text-[12px]" style={{ color: tokens.sub }}>Internal administrator view</p></div>
        <button onClick={() => void load(range)} aria-label="Refresh" className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: tokens.chipBg, border: tokens.chipBorder }}><RefreshCw className={status === "loading" ? "animate-spin" : ""} /></button>
      </header>
      <div className="flex gap-2 px-5 py-3">
        {ranges.map((option) => <button key={option.id} onClick={() => setRange(option.id)} className="px-3 py-1.5 rounded-full text-[12px] font-bold" style={{ color: range === option.id ? ACCENT : tokens.sub, border: `1px solid ${range === option.id ? ACCENT : tokens.divider}` }}>{option.label}</button>)}
      </div>
      <main className="flex-1 overflow-y-auto px-5 pb-10">
        {status === "loading" && <p className="py-12 text-center" style={{ color: tokens.sub }}>Loading platform analytics...</p>}
        {status === "error" && <div className="py-12 text-center"><ShieldAlert className="mx-auto mb-3" style={{ color: "#f87171" }} /><p className="font-bold" style={{ color: tokens.heading }}>Platform analytics unavailable</p><p className="text-[13px] mt-1" style={{ color: tokens.sub }}>You may not have administrator access, or the service is unavailable.</p></div>}
        {status === "ready" && data && <>
          <section className="grid grid-cols-2 gap-3 mb-4">
              {[
                ["Total users", data.platformAnalytics.totalUsers],
                ["Total creators", data.platformAnalytics.totalCreators],
                ["Active users", data.platformAnalytics.activeUsers],
                ["Active creators", data.platformAnalytics.activeCreators],
                ["New users", data.platformAnalytics.newUsers],
                ["New creators", data.platformAnalytics.newCreators],
                ["Views", data.platformAnalytics.totalViews],
                ["Published videos", data.platformAnalytics.totalPublishedVideos],
                ["Engagement", `${data.platformAnalytics.engagementRate.toFixed(1)}%`]
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-2xl p-3.5" style={{ background: tokens.groupBg, border: tokens.groupBorder }}>
                  <p className="font-extrabold text-[20px]" style={{ color: tokens.heading }}>{value}</p>
                  <p className="text-[12px]" style={{ color: tokens.sub }}>{label}</p>
                </div>
              ))}
          </section>
            <section className="rounded-2xl p-4 mb-4" style={{ background: tokens.groupBg, border: tokens.groupBorder }}>
              <h2 className="font-bold mb-3" style={{ color: tokens.heading }}>Period comparison</h2>
              <div className="grid grid-cols-2 gap-2 text-[12px]" style={{ color: tokens.sub }}>
                {[
                  ["Users", data.platformAnalytics.comparison?.userGrowthPct],
                  ["Creators", data.platformAnalytics.comparison?.creatorGrowthPct],
                  ["Content", data.platformAnalytics.comparison?.contentGrowthPct],
                  ["Views", data.platformAnalytics.comparison?.viewsGrowthPct],
                  ["Engagement", data.platformAnalytics.comparison?.engagementGrowthPct],
                  ["Active users", data.platformAnalytics.comparison?.activeUsersGrowthPct]
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex justify-between">
                    <span>{label}</span>
                    <span>{typeof value === "number" ? `${value >= 0 ? "+" : ""}${value.toFixed(1)}%` : "-"}</span>
                  </div>
                ))}
              </div>
            </section>
            <section className="rounded-2xl p-4 mb-4" style={{ background: tokens.groupBg, border: tokens.groupBorder }}>
              <h2 className="font-bold mb-3" style={{ color: tokens.heading }}>Audience and content health</h2>
              <div className="grid grid-cols-2 gap-2 text-[12px]" style={{ color: tokens.sub }}>
                <span>Daily active users: {data.platformAnalytics.dailyActiveUsers ?? "-"}</span>
                <span>Weekly active users: {data.platformAnalytics.weeklyActiveUsers ?? "-"}</span>
                <span>Monthly active users: {data.platformAnalytics.monthlyActiveUsers ?? "-"}</span>
                <span>Approved content: {data.platformAnalytics.approvedContent}</span>
                <span>Flagged content: {data.platformAnalytics.flaggedContent}</span>
                <span>Removed content: {data.platformAnalytics.removedContent}</span>
              </div>
            </section>
          <section className="rounded-2xl p-4 mb-4" style={{ background: tokens.groupBg, border: tokens.groupBorder }}>
            <div className="flex items-start justify-between gap-3 mb-3">
              <div><h2 className="font-bold" style={{ color: tokens.heading }}>Activity trend</h2><p className="text-[11px] mt-1" style={{ color: tokens.sub }}>Daily aggregate activity</p></div>
              <select aria-label="Trend metric" value={trendKey} onChange={(event) => setTrendKey(event.target.value as TrendKey)} className="rounded-lg px-2 py-1 text-[11px]" style={{ color: tokens.heading, background: tokens.chipBg, border: tokens.chipBorder }}>
                <option value="views">Views</option><option value="engagement">Engagement</option><option value="uploads">Uploads</option><option value="publishedVideos">Published</option>
              </select>
            </div>
            {data.platformAnalyticsTrends.length === 0 ? <p className="text-[13px]" style={{ color: tokens.sub }}>No activity in this period.</p> : <TrendChart points={data.platformAnalyticsTrends} metric={trendKey} tokens={tokens} />}
          </section>
          <section>
            <div className="flex items-center justify-between gap-3 mb-3"><h2 className="font-bold" style={{ color: tokens.heading }}>Top content</h2><select aria-label="Sort top content" value={sortBy} onChange={(event) => setSortBy(event.target.value as TopSort)} className="rounded-lg px-2 py-1 text-[11px]" style={{ color: tokens.heading, background: tokens.chipBg, border: tokens.chipBorder }}>{topSorts.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></div>
            {data.platformTopContent.length === 0 ? <p className="text-[13px]" style={{ color: tokens.sub }}>No published content activity in this period.</p> : <div className="space-y-2">{data.platformTopContent.map((item) => <div key={item.post.id} className="rounded-2xl p-3" style={{ background: tokens.groupBg, border: tokens.groupBorder }}><p className="font-semibold text-[13px] truncate" style={{ color: tokens.heading }}>{item.post.caption || "Untitled post"}</p><p className="text-[12px] mt-1" style={{ color: tokens.sub }}>{item.views} views · {item.likes} likes · {item.engagementRate.toFixed(1)}% engagement{item.completionRate == null ? "" : ` · ${item.completionRate.toFixed(1)}% complete`}</p></div>)}</div>}
          </section>
        </>}
      </main>
    </div>
  );
}

function TrendChart({ points, metric, tokens }: { points: PlatformData["platformAnalyticsTrends"]; metric: TrendKey; tokens: ReturnType<typeof useTokens> }) {
  const visible = points.slice(-28);
  const values = visible.map((point) => point[metric]);
  const max = Math.max(...values, 1);
  const width = 640;
  const height = 190;
  const path = values.map((value, index) => {
    const x = visible.length === 1 ? width / 2 : (index / (visible.length - 1)) * width;
    const y = height - (value / max) * (height - 20) - 10;
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  return <div><svg viewBox={`0 0 ${width} ${height}`} className="w-full h-44" role="img" aria-label={`${metric} trend`} preserveAspectRatio="none"><line x1="0" y1={height - 10} x2={width} y2={height - 10} stroke={tokens.divider} /><path d={path} fill="none" stroke={ACCENT} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" /></svg><div className="flex justify-between text-[11px]" style={{ color: tokens.sub }}><span>{visible[0]?.date ?? ""}</span><span>{visible[visible.length - 1]?.date ?? ""}</span></div></div>;
}
