export type AnalyticsRange = "7d" | "30d" | "90d";

const RANGE_DAYS: Record<AnalyticsRange, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
};

export function analyticsPeriod(range: AnalyticsRange, end = new Date()) {
  const start = new Date(end.getTime() - RANGE_DAYS[range] * 86_400_000);
  return { start: start.toISOString(), end: end.toISOString() };
}
