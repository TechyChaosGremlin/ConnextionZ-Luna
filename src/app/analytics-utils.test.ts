import test from "node:test";
import assert from "node:assert/strict";

import { analyticsPeriod } from "./analytics-utils.ts";

const end = new Date("2026-09-06T12:00:00.000Z");

test("builds the 7-day creator analytics period", () => {
  assert.deepEqual(analyticsPeriod("7d", end), {
    start: "2026-08-30T12:00:00.000Z",
    end: "2026-09-06T12:00:00.000Z",
  });
});

test("builds the 30-day creator analytics period", () => {
  assert.deepEqual(analyticsPeriod("30d", end), {
    start: "2026-08-07T12:00:00.000Z",
    end: "2026-09-06T12:00:00.000Z",
  });
});

test("builds the 90-day creator analytics period", () => {
  assert.deepEqual(analyticsPeriod("90d", end), {
    start: "2026-06-08T12:00:00.000Z",
    end: "2026-09-06T12:00:00.000Z",
  });
});
