import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";

import { PAID_FEED_QUERY, fetchPaidFeedPage, type PaidFeedItem } from "./paid-feed-graphql.ts";
import { createPaidFeedStore } from "./paid-feed-store.ts";
import { fetchFeedPageFromApi } from "./profile-graphql.ts";

const originalFetch = globalThis.fetch;
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

beforeEach(() => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true, value: new MemoryStorage(),
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true, value: { onLine: true },
  });
});

after(() => {
  globalThis.fetch = originalFetch;
  if (originalStorage) Object.defineProperty(globalThis, "sessionStorage", originalStorage);
  else Reflect.deleteProperty(globalThis, "sessionStorage");
  if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
  else Reflect.deleteProperty(globalThis, "navigator");
});

function item(id: string, paidCampaignId: string = `campaign-${id}`): PaidFeedItem {
  return {
    id, caption: `Paid ${id}`, thumbnail: "thumbnail.jpg", mediaUrl: "video.mp4",
    views: 12, likes: 3, comments: 1, shares: 0, saves: 0,
    creator: { id: "creator", username: "creator", displayName: "Creator" },
    isSponsored: true, sponsoredLabel: "Sponsored", paidCampaignId,
    paidDeliveryId: `delivery-${id}`,
    paidInteractionContext: `context-${id}`,
  };
}

function page(items: PaidFeedItem[], nextCursor: string | null = null) {
  return Response.json({ data: { feed: { items, nextCursor } } });
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((settle) => { resolve = settle; });
  return { promise, resolve };
}

test("Paid query uses the shared authenticated client, requested size, and only PAID", async () => {
  const payload = btoa(JSON.stringify({ sub: "viewer", exp: Math.floor(Date.now() / 1000) + 900 }));
  const token = `header.${payload}.signature`;
  sessionStorage.setItem("connextionz.accessToken", JSON.stringify(token));
  let requests = 0;
  globalThis.fetch = async (input, init) => {
    requests += 1;
    assert.equal(new URL(String(input)).pathname, "/graphql");
    assert.equal(init?.method, "POST");
    assert.equal(init?.credentials, "include");
    assert.equal((init?.headers as Record<string, string>).Authorization, `Bearer ${token}`);
    const body = JSON.parse(String(init?.body));
    assert.equal(body.query, PAID_FEED_QUERY);
    assert.match(body.query, /filter: \{ algorithm: PAID \}/);
    assert.match(body.query, /isSponsored sponsoredLabel paidCampaignId/);
    assert.match(body.query, /paidDeliveryId paidInteractionContext/);
    assert.doesNotMatch(body.query, /mutation|trackPostWatch|following:|ORGANIC|VIRAL|COMMUNITY/);
    assert.deepEqual(body.variables, { cursor: null, limit: 7 });
    return page([item("one")], "server-cursor");
  };
  assert.deepEqual(await fetchPaidFeedPage(null, 7), {
    ok: true, value: { items: [item("one")], nextCursor: "server-cursor" },
  });
  assert.equal(requests, 1);
});

test("empty Paid response is ready/exhausted, with no seeded or normal-feed fallback", async () => {
  let requests = 0;
  globalThis.fetch = async () => { requests += 1; return page([]); };
  const store = createPaidFeedStore();
  await store.reload();
  assert.deepEqual(store.getSnapshot(), {
    items: [], status: "ready", error: "", loadingMore: false, reachedEnd: true,
  });
  await store.loadMore();
  assert.equal(requests, 1);
});

test("pagination sends opaque server cursors unchanged and appends without post/campaign duplicates", async () => {
  const cursor = "paid1.viewer.anchor.snapshot.signature+/=%2F";
  const variables: unknown[] = [];
  globalThis.fetch = async (_input, init) => {
    variables.push(JSON.parse(String(init?.body)).variables);
    if (variables.length === 1) return page([item("one"), item("two")], cursor);
    return page([
      item("two"), item("same-campaign", "campaign-one"),
      item("one", "different-campaign"), item("three"), item("three"),
    ]);
  };
  const store = createPaidFeedStore(2);
  await store.reload();
  await store.loadMore();
  await store.loadMore();
  assert.deepEqual(variables, [{ cursor: null, limit: 2 }, { cursor, limit: 2 }]);
  assert.deepEqual(store.getSnapshot().items.map((post) => post.id), ["one", "two", "three"]);
  assert.equal(store.getSnapshot().reachedEnd, true);
});

test("loading and loading-more states publish changes and prevent concurrent requests", async () => {
  const first = deferredResponse();
  const second = deferredResponse();
  let requests = 0;
  globalThis.fetch = () => { requests += 1; return requests === 1 ? first.promise : second.promise; };
  const store = createPaidFeedStore();
  const snapshots: string[] = [];
  const unsubscribe = store.subscribe(() => { snapshots.push(store.getSnapshot().status); });
  const initial = store.reload();
  assert.equal(store.getSnapshot().status, "loading");
  await store.loadMore();
  assert.equal(requests, 1);
  first.resolve(page([item("one")], "opaque"));
  await initial;
  const more = store.loadMore();
  assert.equal(store.getSnapshot().loadingMore, true);
  assert.deepEqual(store.getSnapshot().items, [item("one")]);
  await store.loadMore();
  assert.equal(requests, 2);
  second.resolve(page([item("two")]));
  await more;
  assert.equal(store.getSnapshot().loadingMore, false);
  assert.equal(snapshots.at(-1), "ready");
  unsubscribe();
});

test("delivery-disabled response remains an explicit error and retry does not enable delivery", async () => {
  let requests = 0;
  globalThis.fetch = async (_input, init) => {
    requests += 1;
    assert.deepEqual(JSON.parse(String(init?.body)).variables, { cursor: null, limit: 10 });
    return Response.json({
      data: null,
      errors: [{
        message: "Paid feed delivery is disabled",
        extensions: { code: "NOT_IMPLEMENTED", statusCode: 501 },
      }],
    });
  };
  const store = createPaidFeedStore();
  await store.reload();
  assert.equal(store.getSnapshot().status, "error");
  assert.equal(store.getSnapshot().error, "Paid feed delivery is disabled");
  assert.deepEqual(store.getSnapshot().items, []);
  await store.loadMore();
  assert.equal(requests, 1);
  await store.reload();
  assert.equal(requests, 2);
  assert.equal(store.getSnapshot().status, "error");
});

test("failed continuation preserves items and the same server cursor for retry", async () => {
  const cursor = "paid1.unchanged.server-signed";
  const variables: unknown[] = [];
  globalThis.fetch = async (_input, init) => {
    variables.push(JSON.parse(String(init?.body)).variables);
    if (variables.length === 1) return page([item("one")], cursor);
    if (variables.length === 2) return Response.json({ errors: [{ message: "Try again" }] });
    return page([item("two")]);
  };
  const store = createPaidFeedStore();
  await store.reload();
  await store.loadMore();
  assert.deepEqual(store.getSnapshot().items, [item("one")]);
  assert.equal(store.getSnapshot().status, "ready");
  assert.equal(store.getSnapshot().error, "Try again");
  assert.equal(store.getSnapshot().reachedEnd, false);
  assert.equal(store.getSnapshot().loadingMore, false);
  await store.loadMore();
  assert.deepEqual(variables.slice(1), [{ cursor, limit: 10 }, { cursor, limit: 10 }]);
  assert.equal(store.getSnapshot().error, "");
  assert.deepEqual(store.getSnapshot().items, [item("one"), item("two")]);
});

test("a failed initial request can reload successfully without retaining an error or duplicates", async () => {
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    if (requests === 1) return Response.json({ errors: [{ message: "Temporarily unavailable" }] });
    return page([item("one"), item("one")]);
  };
  const store = createPaidFeedStore();
  await store.reload();
  assert.equal(store.getSnapshot().status, "error");
  assert.equal(store.getSnapshot().error, "Temporarily unavailable");
  await store.reload();
  assert.deepEqual(store.getSnapshot(), {
    items: [item("one")], status: "ready", error: "", loadingMore: false, reachedEnd: true,
  });
});

test("network, HTTP, invalid JSON, and malformed data errors never become an empty success", async () => {
  const responses: (() => Promise<Response>)[] = [
    async () => { throw new Error("offline"); },
    async () => new Response("unavailable", { status: 503 }),
    async () => new Response("not json"),
    async () => Response.json({ data: { feed: null } }),
    async () => Response.json({ data: {} }),
    async () => Response.json({ data: { feed: { items: [] } } }),
  ];
  for (const response of responses) {
    globalThis.fetch = response;
    const result = await fetchPaidFeedPage();
    assert.equal(result.ok, false);
    if (!result.ok) assert.ok(result.error);
  }
});

test("offline and invalid page size errors are explicit and make no request", async () => {
  globalThis.fetch = async () => { throw new Error("must not request"); };
  for (const limit of [0, -1, 101, 1.5]) {
    const result = await fetchPaidFeedPage(null, limit);
    assert.equal(result.ok, false);
    if (!result.ok) assert.match(result.error, /page size/);
  }
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: false } });
  assert.deepEqual(await fetchPaidFeedPage(), {
    ok: false, error: "You're offline. Reconnect to load the Paid feed.",
  });
});

test("reload invalidates stale in-flight pages and begins a fresh cursor sequence", async () => {
  const stale = deferredResponse();
  const current = deferredResponse();
  let requests = 0;
  globalThis.fetch = (_input, init) => {
    assert.deepEqual(JSON.parse(String(init?.body)).variables, { cursor: null, limit: 10 });
    requests += 1;
    return requests === 1 ? stale.promise : current.promise;
  };
  const store = createPaidFeedStore();
  const previous = store.reload();
  const reload = store.reload();
  stale.resolve(page([item("stale")], "stale-cursor"));
  await previous;
  assert.deepEqual(store.getSnapshot().items, []);
  assert.equal(store.getSnapshot().status, "loading");
  current.resolve(page([item("current")]));
  await reload;
  assert.deepEqual(store.getSnapshot().items, [item("current")]);
  assert.equal(store.getSnapshot().reachedEnd, true);
});

test("Paid state is instance-local and does not change normal feed queries or results", async () => {
  const normalPost = {
    id: "organic", caption: "Organic", thumbnail: "organic.jpg", views: 1, likes: 0,
    creator: { id: "creator", username: "creator", displayName: "Creator" },
  };
  const normalQueries: string[] = [];
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body));
    if (body.query === PAID_FEED_QUERY) return page([item("paid")]);
    normalQueries.push(body.query);
    assert.deepEqual(body.variables, { cursor: null, limit: 10, following: false });
    return Response.json({ data: { feed: { items: [normalPost], nextCursor: null } } });
  };
  const before = await fetchFeedPageFromApi(null);
  const paid = createPaidFeedStore();
  const separateViewer = createPaidFeedStore();
  await paid.reload();
  const after = await fetchFeedPageFromApi(null);
  assert.deepEqual(after, before);
  assert.deepEqual(after?.items, [normalPost]);
  assert.equal(normalQueries[0], normalQueries[1]);
  assert.doesNotMatch(normalQueries[0], /PAID|paidCampaignId|isSponsored/);
  assert.deepEqual(paid.getSnapshot().items, [item("paid")]);
  assert.deepEqual(separateViewer.getSnapshot().items, []);
});
