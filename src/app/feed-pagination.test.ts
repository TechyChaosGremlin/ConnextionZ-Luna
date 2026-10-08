import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldLoadNextFeedPage } from "./feed-pagination.ts";

test("continues after a bounded empty page when a cursor remains", () => {
  assert.equal(shouldLoadNextFeedPage(0, 0, "next-page", false), true);
});

test("does not continue after an empty terminal page", () => {
  assert.equal(shouldLoadNextFeedPage(0, 0, null, true), false);
});

test("does not continue an empty initial page before a cursor exists", () => {
  assert.equal(shouldLoadNextFeedPage(0, 0, null, false), false);
});

test("preserves near-end prefetch for non-empty pages", () => {
  assert.equal(shouldLoadNextFeedPage(2, 4, "next-page", false), true);
});
