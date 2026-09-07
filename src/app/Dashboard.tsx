// ─── CREATOR DASHBOARD ───────────────────────────────────────────────────────
//
// What the viewer's work is actually doing. Three tabs, because a creator asks
// three different questions here and mixing them makes all three harder:
//
//   Overview  — how the account is performing, and against what it did before
//   Content   — how each post is performing, and what to do about it
//   Collabs   — the pipeline: requests in, accepted, completed, opportunities
//
// Every number comes from `dashboard-data.ts`, which derives them from the same
// records the profile and feed render, so nothing here can quietly disagree with
// the rest of the app. The charts are hand-drawn SVG rather than a chart
// library: they are small, they inherit the app's tokens in both themes, and
// they do not ship a second rendering model into the bundle.

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  ArrowLeft, BarChart3, Bookmark, Eye, Heart, Loader2, MessageCircle,
  Navigation, RefreshCw, Sparkles, TrendingDown, TrendingUp, Trophy,
  UserPlus, Users, WifiOff,
} from "lucide-react";
import { useTheme } from "./ThemeContext";
import { ACCENT, EmptyState, useTokens } from "./settings-ui";
import { SegmentedTabs, Thumb, formatCount } from "./profile-ui";
import { useViewer } from "./session";
import {
  type ContentRow, type DashboardData, type Metric, type MetricKey, type Range,
  RANGES, axisLabels, fetchDashboard, signed,
} from "./dashboard-data";

type Tab = "overview" | "content" | "collabs";
type Status = "loading" | "ready" | "error";
type Sort = "recent" | "views" | "likes";
type Trend = "views" | "engagement" | "followers";

const METRIC_ICON: Record<MetricKey, typeof Eye> = {
  views: Eye,
  likes: Heart,
  comments: MessageCircle,
  shares: Navigation,
  followers: UserPlus,
};

export function DashboardScreen({
  onBack, onOpenPost, canOpenPost, onSharePost, onOpenRequests,
}: {
  onBack: () => void;
  onOpenPost?: (postId: string) => void;
  canOpenPost?: (postId: string) => boolean;
  onSharePost?: (postId: string) => void;
  /** The Inbox's collab requests tab — where a pending request is answered. */
  onOpenRequests?: () => void;
}) {
  const isDark = useTheme();
  const t = useTokens(isDark);
  const viewer = useViewer();
  // Publishing or deleting a post has to move these numbers immediately.

  const [tab, setTab] = useState<Tab>("overview");
  const [range, setRange] = useState<Range>("30d");
  const [status, setStatus] = useState<Status>("loading");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  const load = async (next: Range) => {
    setStatus("loading");
    setError("");
    const result = await fetchDashboard(next);
    if (!result.ok) {
      setError(typeof navigator !== "undefined" && !navigator.onLine
        ? "You appear to be offline. Reconnect and try again."
        : result.error);
      setStatus("error");
      return;
    }
    setData(result.value);
    setStatus("ready");
  };

  useEffect(() => { void load(range); }, [range]);

  return (
    <div className="absolute inset-0 z-30 flex flex-col" style={{ background: t.bg }}>
      {/* ── Header ── */}
      <div className="flex items-center gap-3 px-5 pt-14 pb-4 flex-shrink-0"
        style={{ borderBottom: `1px solid ${t.divider}` }}>
        <button onClick={onBack} aria-label="Back"
          className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 active:opacity-70"
          style={{ background: t.backBtnBg, border: t.cardBorder }}>
          <ArrowLeft className="w-4 h-4" style={{ color: t.heading }} />
        </button>
        <div className="min-w-0 flex-1">
          <h1 className="font-extrabold text-[22px] leading-tight" style={{ color: t.heading }}>Creator Dashboard</h1>
          <p className="text-[13px] mt-0.5 truncate" style={{ color: t.sub }}>@{viewer.username}</p>
        </div>
        <button onClick={() => void load(range)} aria-label="Refresh"
          className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ background: t.chipBg, border: t.chipBorder }}>
          <RefreshCw className={`w-4 h-4 ${status === "loading" ? "animate-spin" : ""}`} style={{ color: t.sub }} />
        </button>
      </div>

      {/* ── Range ── */}
      <div className="flex gap-2 px-5 py-3 flex-shrink-0">
        {RANGES.map((option) => {
          const on = range === option.id;
          return (
            <button key={option.id} onClick={() => setRange(option.id)}
              className="px-3.5 py-1.5 rounded-full text-[12px] font-bold"
              style={{
                background: on ? "rgba(0,174,239,0.2)" : t.chipBg,
                border: on ? `1px solid ${ACCENT}` : t.chipBorder,
                color: on ? ACCENT : t.sub,
              }}>
              Last {option.label}
            </button>
          );
        })}
      </div>

      <SegmentedTabs layoutId="dashboard-tabs" active={tab} onChange={setTab}
        tabs={[
          { id: "overview" as Tab, label: "Overview" },
          { id: "content" as Tab, label: "Content", count: data?.content.length },
          { id: "collabs" as Tab, label: "Collabs", count: data?.collab.totalRequests ?? undefined },
        ]} />

      <div className="flex-1 overflow-y-auto px-5 pt-4 pb-12" style={{ scrollbarWidth: "none" }}>
        {status === "loading" && <DashboardSkeleton t={t} />}

        {status === "error" && (
          <div className="flex flex-col items-center text-center py-16 px-4">
            <div className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4"
              style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.28)", color: "#f87171" }}>
              <WifiOff className="w-7 h-7" />
            </div>
            <p className="font-bold text-[16px]" style={{ color: t.heading }}>Analytics didn't load</p>
            <p className="text-[13px] mt-1.5 leading-relaxed max-w-[270px]" style={{ color: t.sub }}>{error}</p>
            <button onClick={() => void load(range)}
              className="mt-5 px-5 py-2.5 rounded-full text-[13px] font-bold text-white flex items-center gap-2"
              style={{ background: `linear-gradient(135deg,${ACCENT},#0077cc)`, boxShadow: "0 6px 18px rgba(0,174,239,0.35)" }}>
              <RefreshCw className="w-3.5 h-3.5" /> Try again
            </button>
          </div>
        )}

        {status === "ready" && data && (
          <AnimatePresence mode="wait">
            <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.18 }}>
              {tab === "overview" && data.content.length === 0 && data.metrics.every((metric) => metric.value === 0) ? (
                <EmptyState icon={<BarChart3 className="w-7 h-7" />} t={t}
                  title="No analytics yet"
                  body="Publish content and your views, engagement, and watch metrics will appear here." />
              ) : tab === "overview" ? (
                <Overview data={data} t={t} onOpenPost={onOpenPost} canOpenPost={canOpenPost} />
              ) : null}
              {tab === "content" && (
                <Content data={data} t={t} onOpenPost={onOpenPost} canOpenPost={canOpenPost}
                  onSharePost={onSharePost} />
              )}
              {tab === "collabs" && <Collabs data={data} t={t} onOpenRequests={onOpenRequests} />}
            </motion.div>
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}

// ─── OVERVIEW ────────────────────────────────────────────────────────────────

function Overview({
  data, t, onOpenPost, canOpenPost,
}: {
  data: DashboardData;
  t: ReturnType<typeof useTokens>;
  onOpenPost?: (postId: string) => void;
  canOpenPost?: (postId: string) => boolean;
}) {
  const views = data.metrics.find((m) => m.key === "views")!;
  const rest = data.metrics.filter((m) => m.key !== "views");
  const [trend, setTrend] = useState<Trend>("views");
  const labels = useMemo(() => axisLabels(data.days, data.generatedAt), [data.days, data.generatedAt]);
  const trendSeries = trend === "views"
    ? views.series
    : trend === "followers"
      ? data.metrics.find((m) => m.key === "followers")!.series
      : data.metrics.filter((m) => m.key !== "followers").reduce((series, metric) => series.map((value, i) => value + metric.series[i]), Array.from({ length: data.days }, () => 0));

  const engagement = rest.filter((m) => m.key !== "followers");
  const engagementTotal = engagement.reduce((n, m) => n + m.value, 0) || 1;

  return (
    <>
      {/* ── Views over time ── */}
      <div className="rounded-3xl p-4 mb-4" style={{ background: t.groupBg, border: t.groupBorder }}>
        <div className="flex items-start justify-between mb-1">
          <div>
            <div className="flex gap-2 mb-2">
              {(["views", "engagement", "followers"] as Trend[]).map((option) => (
                <button key={option} onClick={() => setTrend(option)} className="px-2.5 py-1 rounded-full text-[11px] font-bold"
                  style={{ color: trend === option ? ACCENT : t.sub, border: `1px solid ${trend === option ? ACCENT : t.divider}` }}>
                  {option[0].toUpperCase() + option.slice(1)}
                </button>
              ))}
            </div>
            <p className="text-[11px] font-bold uppercase tracking-widest" style={{ color: t.sectionLbl }}>{trend}</p>
            <p className="font-extrabold text-[30px] leading-tight mt-1" style={{ color: t.heading }}>
              {formatCount(trend === "views" ? views.value : trendSeries.reduce((sum, value) => sum + value, 0))}
            </p>
          </div>
          <DeltaPill delta={views.deltaPct} />
        </div>
        <p className="text-[12px] mb-3" style={{ color: t.sub }}>
          Last {data.days} days, against the {data.days} before them
        </p>
        <LineChart series={trendSeries} labels={labels} t={t} />
      </div>

      {/* ── The rest of the counters ── */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        {rest.map((metric) => <MetricCard key={metric.key} metric={metric} t={t} />)}
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <QualityCard label="Unique viewers" value={formatCount(data.quality.uniqueViewers)} t={t} />
        <QualityCard label="Saves" value={formatCount(data.quality.saves)} t={t} />
        <QualityCard label="Avg watch time" value={data.quality.avgWatchTime == null ? "Unavailable" : `${data.quality.avgWatchTime.toFixed(1)}s`} t={t} />
        <QualityCard label="Completion rate" value={data.quality.completionRate == null ? "Unavailable" : `${data.quality.completionRate.toFixed(1)}%`} t={t} />
        <QualityCard label="Engagement rate" value={`${data.quality.engagementRate.toFixed(1)}%`} t={t} />
      </div>

      {/* ── Engagement mix ── */}
      <div className="rounded-3xl p-4 mb-4" style={{ background: t.groupBg, border: t.groupBorder }}>
        <p className="text-[11px] font-bold uppercase tracking-widest mb-3" style={{ color: t.sectionLbl }}>
          Where the engagement came from
        </p>
        {engagement.map((metric) => {
          const Icon = METRIC_ICON[metric.key];
          const pct = Math.round((metric.value / engagementTotal) * 100);
          return (
            <div key={metric.key} className="mb-3 last:mb-0">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[13px] font-semibold flex items-center gap-1.5" style={{ color: t.body }}>
                  <Icon className="w-3.5 h-3.5" style={{ color: ACCENT }} /> {metric.label}
                </span>
                <span className="text-[12px] font-bold" style={{ color: t.sub }}>
                  {formatCount(metric.value)} · {pct}%
                </span>
              </div>
              <div className="h-2 rounded-full overflow-hidden" style={{ background: t.chipBg }}>
                <motion.div initial={{ width: 0 }} animate={{ width: `${pct}%` }}
                  transition={{ duration: 0.6, ease: "easeOut" }}
                  className="h-full rounded-full" style={{ background: `linear-gradient(90deg,${ACCENT},#7c3aed)` }} />
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Best post ── */}
      {data.best && (
        <div className="rounded-3xl p-4" style={{ background: t.cardBg, border: t.cardBorder }}>
          <p className="text-[11px] font-bold uppercase tracking-widest mb-3 flex items-center gap-1.5" style={{ color: ACCENT }}>
            <Trophy className="w-3.5 h-3.5" /> Best performing
          </p>
          <ContentCard row={data.best} t={t} onOpenPost={onOpenPost} canOpenPost={canOpenPost} />
        </div>
      )}
    </>
  );
}

function MetricCard({ metric, t }: { metric: Metric; t: ReturnType<typeof useTokens> }) {
  const Icon = METRIC_ICON[metric.key];
  return (
    <div className="rounded-2xl p-3.5" style={{ background: t.groupBg, border: t.groupBorder }}>
      <div className="flex items-center justify-between mb-1">
        <span className="w-7 h-7 rounded-lg flex items-center justify-center"
          style={{ background: "rgba(0,174,239,0.14)", color: ACCENT }}>
          <Icon className="w-3.5 h-3.5" />
        </span>
        <DeltaPill delta={metric.deltaPct} small />
      </div>
      <p className="font-extrabold text-[20px] leading-tight" style={{ color: t.heading }}>{formatCount(metric.value)}</p>
      <p className="text-[12px] mb-2" style={{ color: t.sub }}>{metric.label}</p>
      <Sparkline series={metric.series} />
    </div>
  );
}

function QualityCard({ label, value, t }: { label: string; value: string; t: ReturnType<typeof useTokens> }) {
  return (
    <div className="rounded-2xl p-3.5" style={{ background: t.groupBg, border: t.groupBorder }}>
      <p className="font-extrabold text-[18px] leading-tight" style={{ color: t.heading }}>{value}</p>
      <p className="text-[12px] mt-1" style={{ color: t.sub }}>{label}</p>
    </div>
  );
}

function DeltaPill({ delta, small = false }: { delta: number; small?: boolean }) {
  const up = delta >= 0;
  const Icon = up ? TrendingUp : TrendingDown;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-bold ${small ? "px-1.5 py-0.5 text-[10px]" : "px-2.5 py-1 text-[12px]"}`}
      style={{
        background: up ? "rgba(34,197,94,0.14)" : "rgba(239,68,68,0.12)",
        border: `1px solid ${up ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.3)"}`,
        color: up ? "#4ade80" : "#f87171",
      }}>
      <Icon className={small ? "w-2.5 h-2.5" : "w-3 h-3"} /> {signed(delta)}
    </span>
  );
}

// ─── CHARTS ──────────────────────────────────────────────────────────────────

/**
 * A filled line, drawn in a fixed viewBox and scaled by CSS — so it is sharp at
 * any width and needs no measurement pass. Only the first, middle and last axis
 * labels are drawn: a 90-day window cannot legibly show ninety of them.
 */
function LineChart({
  series, labels, t,
}: {
  series: number[]; labels: string[]; t: ReturnType<typeof useTokens>;
}) {
  const W = 300;
  const H = 110;
  const max = Math.max(...series, 1);
  const step = series.length > 1 ? W / (series.length - 1) : W;
  const points = series.map((value, i) => [i * step, H - (value / max) * (H - 12) - 6] as const);
  const line = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const [lastX, lastY] = points[points.length - 1] ?? [0, H];
  const ticks = [0, Math.floor(labels.length / 2), labels.length - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: 110 }}
        role="img" aria-label={`Daily views, peaking at ${max}`}>
        <defs>
          <linearGradient id="dash-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={ACCENT} stopOpacity="0.35" />
            <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((fraction) => (
          <line key={fraction} x1="0" x2={W} y1={H * fraction} y2={H * fraction}
            stroke={t.divider} strokeWidth="1" />
        ))}
        <path d={area} fill="url(#dash-fill)" />
        <path d={line} fill="none" stroke={ACCENT} strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        <circle cx={lastX} cy={lastY} r="3.5" fill={ACCENT} />
      </svg>
      <div className="flex justify-between mt-1.5">
        {ticks.map((index) => (
          <span key={index} className="text-[10px]" style={{ color: t.sub }}>{labels[index]}</span>
        ))}
      </div>
    </div>
  );
}

/** The same line, small enough to sit inside a stat card. */
function Sparkline({ series }: { series: number[] }) {
  const W = 100;
  const H = 24;
  const max = Math.max(...series, 1);
  const step = series.length > 1 ? W / (series.length - 1) : W;
  const line = series
    .map((value, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(H - (value / max) * (H - 4) - 2).toFixed(1)}`)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: 24 }} aria-hidden>
      <path d={line} fill="none" stroke={ACCENT} strokeWidth="1.6" strokeLinecap="round"
        strokeLinejoin="round" vectorEffect="non-scaling-stroke" opacity="0.9" />
    </svg>
  );
}

// ─── CONTENT MANAGEMENT ──────────────────────────────────────────────────────

const SORTS: { id: Sort; label: string }[] = [
  { id: "recent", label: "Newest" },
  { id: "views", label: "Most viewed" },
  { id: "likes", label: "Most liked" },
];

function Content({
  data, t, onOpenPost, canOpenPost, onSharePost,
}: {
  data: DashboardData;
  t: ReturnType<typeof useTokens>;
  onOpenPost?: (postId: string) => void;
  canOpenPost?: (postId: string) => boolean;
  onSharePost?: (postId: string) => void;
}) {
  const [sort, setSort] = useState<Sort>("recent");

  const rows = useMemo(() => {
    const list = [...data.content];
    if (sort === "views") return list.sort((a, b) => b.views - a.views);
    if (sort === "likes") return list.sort((a, b) => b.likes - a.likes);
    return list.sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
  }, [data.content, sort]);

  if (!rows.length) {
    return (
      <EmptyState icon={<BarChart3 className="w-7 h-7" />} t={t}
        title="Nothing published yet"
        body="Post a video and its views, likes and comments will show up here within the hour." />
    );
  }

  return (
    <>
      <div className="flex gap-2 mb-4">
        {SORTS.map((option) => {
          const on = sort === option.id;
          return (
            <button key={option.id} onClick={() => setSort(option.id)}
              className="px-3.5 py-1.5 rounded-full text-[12px] font-bold"
              style={{
                background: on ? "rgba(0,174,239,0.2)" : t.chipBg,
                border: on ? `1px solid ${ACCENT}` : t.chipBorder,
                color: on ? ACCENT : t.sub,
              }}>
              {option.label}
            </button>
          );
        })}
      </div>

      <div className="space-y-3">
        {rows.map((row) => (
          <div key={row.id} className="rounded-2xl p-3" style={{ background: t.groupBg, border: t.groupBorder }}>
            <ContentCard row={row} t={t} onOpenPost={onOpenPost} canOpenPost={canOpenPost} />

            <div className="flex items-center gap-2 mt-3">
              {onOpenPost && (!canOpenPost || canOpenPost(row.id)) && (
                <button onClick={() => onOpenPost(row.id)}
                  className="flex-1 h-9 rounded-full text-[12px] font-bold"
                  style={{ background: t.chipBg, border: t.chipBorder, color: t.body }}>
                  Open post
                </button>
              )}
              {onSharePost && (
                <button onClick={() => onSharePost(row.id)}
                  className="flex-1 h-9 rounded-full text-[12px] font-bold"
                  style={{ background: t.chipBg, border: t.chipBorder, color: t.body }}>
                  Share
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function ContentCard({
  row, t, onOpenPost, canOpenPost,
}: {
  row: ContentRow;
  t: ReturnType<typeof useTokens>;
  onOpenPost?: (postId: string) => void;
  canOpenPost?: (postId: string) => boolean;
}) {
  const openable = !!onOpenPost && (!canOpenPost || canOpenPost(row.id));
  const stats: { icon: typeof Eye; value: number }[] = [
    { icon: Eye, value: row.views },
    { icon: Heart, value: row.likes },
    { icon: MessageCircle, value: row.comments },
    { icon: Navigation, value: row.shares },
  ];

  return (
    <div className="flex items-center gap-3">
      <button onClick={openable ? () => onOpenPost!(row.id) : undefined} disabled={!openable}
        className="w-16 h-20 rounded-xl overflow-hidden flex-shrink-0 relative"
        style={{ background: "rgba(0,0,0,0.25)" }}>
        <Thumb src={row.thumbnail} className="absolute inset-0 w-full h-full object-cover" />
        {row.collabWith && (
          <span className="absolute top-1 left-1 px-1.5 py-0.5 rounded-full text-[8px] font-bold"
            style={{ background: "rgba(0,174,239,0.85)", color: "#fff" }}>COLLAB</span>
        )}
      </button>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold line-clamp-2" style={{ color: t.heading }}>
          {row.caption || "No caption"}
        </p>
        <div className="flex items-center gap-3 mt-1.5 flex-wrap">
          {stats.map((stat, i) => {
            const Icon = stat.icon;
            return (
              <span key={i} className="flex items-center gap-1 text-[11px]" style={{ color: t.sub }}>
                <Icon className="w-3 h-3" /> {formatCount(stat.value)}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── COLLABS ─────────────────────────────────────────────────────────────────

function Collabs({
  data, t, onOpenRequests,
}: {
  data: DashboardData;
  t: ReturnType<typeof useTokens>;
  onOpenRequests?: () => void;
}) {
  const c = data.collab;

  const pipeline: { label: string; value: string | number; hint: string; tone?: string }[] = [
    { label: "Requests received", value: c.totalRequests ?? "-", hint: `Last ${data.days} days` },
    { label: "Pending", value: c.pending ?? "-", hint: "Waiting on you", tone: "#f59e0b" },
    { label: "Accepted", value: c.accepted ?? "-", hint: "Accepted or progressed" },
    { label: "Declined", value: c.declined ?? "-", hint: "Turned down" },
    { label: "Cancelled", value: c.cancelled ?? "-", hint: "Closed before completion" },
    { label: "Completed", value: c.completed ?? "-", hint: "Shipped together" },
    { label: "Active projects", value: c.active ?? "-", hint: "In flight now", tone: ACCENT },
  ];

  return (
    <>
      {/* ── Headline three ── */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        {[
          { label: "Acceptance rate", value: c.acceptanceRatePct == null ? "-" : `${c.acceptanceRatePct.toFixed(1)}%`, icon: TrendingUp },
          { label: "Completion rate", value: c.completionRatePct == null ? "-" : `${c.completionRatePct.toFixed(1)}%`, icon: TrendingUp },
          { label: "Avg. reply", value: c.avgResponseHours == null ? "-" : `${c.avgResponseHours.toFixed(1)}h`, icon: Users },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className="rounded-2xl p-3 text-center"
              style={{ background: t.cardBg, border: t.cardBorder }}>
              <Icon className="w-4 h-4 mx-auto mb-1.5" style={{ color: ACCENT }} />
              <p className="font-extrabold text-[18px] leading-none" style={{ color: t.heading }}>{item.value}</p>
              <p className="text-[10px] mt-1.5 leading-tight" style={{ color: t.sub }}>{item.label}</p>
            </div>
          );
        })}
      </div>

      {/* ── Pipeline ── */}
      <p className="text-[11px] font-bold uppercase tracking-widest mb-2 px-1" style={{ color: t.sectionLbl }}>
        Pipeline
      </p>
      <div className="grid grid-cols-2 gap-3 mb-5">
        {pipeline.map((item) => (
          <div key={item.label} className="rounded-2xl p-3.5" style={{ background: t.groupBg, border: t.groupBorder }}>
            <p className="font-extrabold text-[22px] leading-none" style={{ color: item.tone ?? t.heading }}>{item.value}</p>
            <p className="text-[12px] font-semibold mt-1.5" style={{ color: t.body }}>{item.label}</p>
            <p className="text-[11px] mt-0.5" style={{ color: t.sub }}>{item.hint}</p>
          </div>
        ))}
      </div>

      {c.pending != null && c.pending > 0 && onOpenRequests && (
        <motion.button whileTap={{ scale: 0.98 }} onClick={onOpenRequests}
          className="w-full flex items-center gap-3 p-4 rounded-2xl text-left mb-5"
          style={{ background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.32)" }}>
          <span className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: "rgba(245,158,11,0.18)", color: "#f59e0b" }}>
            <Sparkles className="w-5 h-5" />
          </span>
          <span className="flex-1 min-w-0">
            <span className="block font-bold text-[14px]" style={{ color: t.heading }}>
              {c.pending} request{c.pending === 1 ? "" : "s"} waiting on you
            </span>
            <span className="block text-[12px] mt-0.5" style={{ color: t.sub }}>
              Response time is measured when acceptance timestamps are available.
            </span>
          </span>
        </motion.button>
      )}

      {/* ── Daily activity ── */}
      <p className="text-[11px] font-bold uppercase tracking-widest mb-2 px-1" style={{ color: t.sectionLbl }}>
        Daily activity
      </p>
      <div className="rounded-2xl overflow-hidden" style={{ background: t.groupBg, border: t.groupBorder }}>
        {c.trends.filter((point) => point.requested > 0 || point.accepted > 0 || point.completed > 0).slice(-7).map((point, i, rows) => (
          <div key={point.date} className="flex items-center justify-between px-4 py-3.5"
            style={{ borderBottom: i < rows.length - 1 ? `1px solid ${t.divider}` : "none" }}>
            <span className="text-[14px]" style={{ color: t.body }}>{point.date}</span>
            <span className="text-[12px]" style={{ color: t.sub }}>{point.requested} requests · {point.accepted} accepted · {point.completed} completed</span>
          </div>
        ))}
        {c.trends.every((point) => point.requested === 0 && point.accepted === 0 && point.completed === 0) && <p className="px-4 py-3.5 text-[13px]" style={{ color: t.sub }}>No collaboration activity in this period.</p>}
      </div>

      <p className="text-[11px] leading-relaxed mt-4 px-1 flex items-start gap-2" style={{ color: t.sub }}>
        <Bookmark className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
        Figures cover the last {data.days} days and refresh when you open this screen.
      </p>
    </>
  );
}

// ─── LOADING ─────────────────────────────────────────────────────────────────

function DashboardSkeleton({ t }: { t: ReturnType<typeof useTokens> }) {
  const pulse = (delay: number) => ({
    animate: { opacity: [0.35, 0.7, 0.35] },
    transition: { duration: 1.4, repeat: Infinity, delay },
  });
  return (
    <div aria-busy="true" aria-label="Loading analytics">
      <p className="text-[12px] mb-3 flex items-center gap-2" style={{ color: t.sub }}>
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Crunching the numbers…
      </p>
      <motion.div {...pulse(0)} className="rounded-3xl mb-4" style={{ background: t.chipBg, height: 200 }} />
      <div className="grid grid-cols-2 gap-3 mb-4">
        {Array.from({ length: 4 }, (_, i) => (
          <motion.div key={i} {...pulse(i * 0.08)} className="rounded-2xl" style={{ background: t.chipBg, height: 112 }} />
        ))}
      </div>
      <motion.div {...pulse(0.3)} className="rounded-3xl" style={{ background: t.chipBg, height: 150 }} />
    </div>
  );
}
