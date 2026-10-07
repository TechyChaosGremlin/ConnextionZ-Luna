import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, test } from "node:test";
import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  PaidDiscoveryContent, PaidDiscoveryLink, PaidDiscoveryView,
} from "./PaidDiscovery";
import { TrendingSounds } from "./TrendingSounds";
import { createPaidFeedStore, type PaidFeedState } from "./paid-feed-store.ts";
import type { PaidFeedItem } from "./paid-feed-graphql.ts";
import type { Tokens } from "./settings-ui";

const originalFetch = globalThis.fetch;
after(() => { globalThis.fetch = originalFetch; });

const t: Tokens = {
  bg: "#000", heading: "#fff", body: "#fff", sub: "#aaa", sectionLbl: "#aaa",
  groupBg: "#000", groupBorder: "none", divider: "#aaa", chevron: "#aaa",
  cardBg: "#000", cardBorder: "none", fieldBg: "#000", fieldBorder: "none",
  backBtnBg: "#000", chipBg: "#000", chipBorder: "none", switchOff: "#aaa",
};

function post(id: string, changes: Partial<PaidFeedItem> = {}): PaidFeedItem {
  return {
    id, caption: `Sponsored caption ${id}`, thumbnail: "https://example.test/post.jpg",
    views: 0, likes: 0, creator: { id: "creator", username: "paid.creator", displayName: "Paid Creator" },
    isSponsored: true, sponsoredLabel: "Sponsored", paidCampaignId: `campaign-${id}`,
    paidDeliveryId: `delivery-${id}`,
    paidInteractionContext: `context-${id}`,
    ...changes,
  };
}

function state(changes: Partial<PaidFeedState> = {}): PaidFeedState {
  return {
    items: [], status: "ready", error: "", loadingMore: false, reachedEnd: true,
    loadMore: async () => {}, reload: async () => {}, ...changes,
  };
}

function markup(feed: PaidFeedState) {
  return renderToStaticMarkup(<PaidDiscoveryView feed={feed} onBack={() => {}} />);
}

type ButtonProps = { children?: ReactNode; onClick?: () => void; disabled?: boolean };

function buttons(node: ReactNode): ReactElement<ButtonProps>[] {
  const result: ReactElement<ButtonProps>[] = [];
  Children.forEach(node, (child) => {
    if (!isValidElement<ButtonProps>(child)) return;
    if (child.type === "button") result.push(child);
    result.push(...buttons(child.props.children));
  });
  return result;
}

test("dedicated Paid page renders only provided Paid posts and server labels in server order", () => {
  const html = markup(state({ items: [post("second"), post("first", { sponsoredLabel: "Paid promotion" })] }));
  assert.match(html, /Paid Discovery/);
  assert.match(html, /A separate space for paid and sponsored posts/);
  assert.match(html, /aria-label="Paid content: Sponsored"/);
  assert.match(html, /aria-label="Paid content: Paid promotion"/);
  assert.match(html, /Paid Creator/);
  assert.match(html, /@paid.creator/);
  assert.ok(html.indexOf("Sponsored caption second") < html.indexOf("Sponsored caption first"));
  assert.equal((html.match(/<article/g) ?? []).length, 2);
  assert.doesNotMatch(html, /For You|Following|Organic|Viral|Community|Original Sound|Load more/);
});

test("Paid status remains accessible when sponsored metadata is unavailable", () => {
  const html = markup(state({
    items: [post("one", { isSponsored: false, sponsoredLabel: null, paidCampaignId: null })],
  }));
  assert.match(html, /aria-label="Paid content: Paid"/);
  assert.match(html, /aria-label="Paid post by @paid.creator"/);
});

test("post media uses native controls without autoplay or engagement controls", () => {
  const html = markup(state({ items: [post("video", { mediaUrl: "https://example.test/video.mp4" })] }));
  assert.match(html, /<video/);
  assert.match(html, /controls=""/);
  assert.match(html, /preload="none"/);
  assert.doesNotMatch(html, /autoPlay|autoplay|Like|Save|Share|Collab Request/);
});

test("initial loading state is announced and does not render posts or empty success", () => {
  const html = markup(state({ status: "loading", reachedEnd: false }));
  assert.match(html, /role="status"/);
  assert.match(html, /Loading Paid discovery/);
  assert.doesNotMatch(html, /<article|No Paid posts|Try again|Load more/);
});

test("empty Paid page is a distinct success state without fallback content", () => {
  const html = markup(state());
  assert.match(html, /No Paid posts available/);
  assert.match(html, /no sponsored posts to discover right now/);
  assert.match(html, /role="status"/);
  assert.doesNotMatch(html, /<article|couldn&#x27;t load|unavailable|Try again|Load more/);
});

test("initial errors have an accessible alert and retry calls the existing store reload", () => {
  let retries = 0;
  const feed = state({ status: "error", error: "Network unavailable", reload: async () => { retries += 1; } });
  const html = markup(feed);
  assert.match(html, /role="alert"/);
  assert.match(html, /Paid discovery couldn&#x27;t load/);
  assert.match(html, /Network unavailable/);
  const controls = buttons(PaidDiscoveryContent({ feed, t }));
  assert.equal(controls.length, 1);
  controls[0].props.onClick?.();
  assert.equal(retries, 1);
  assert.doesNotMatch(html, /No Paid posts available|<article/);
});

test("delivery-disabled is Paid-unavailable, not empty success, even with stale content", () => {
  let retries = 0;
  const feed = state({
    status: "error", error: "Paid feed delivery is disabled", items: [post("stale")],
    reload: async () => { retries += 1; },
  });
  const html = markup(feed);
  assert.match(html, /Paid discovery is unavailable/);
  assert.match(html, /Paid feed delivery is disabled/);
  assert.match(html, /role="alert"/);
  assert.doesNotMatch(html, /<article|No Paid posts available|Load more/);
  assert.equal(retries, 0);
  buttons(PaidDiscoveryContent({ feed, t }))[0].props.onClick?.();
  assert.equal(retries, 1);
});

test("load more delegates to the Paid store and presents loading-more and exhausted states", () => {
  let loads = 0;
  const feed = state({
    items: [post("one")], reachedEnd: false,
    loadMore: async () => { loads += 1; },
  });
  assert.match(markup(feed), /Load more/);
  buttons(PaidDiscoveryContent({ feed, t }))[0].props.onClick?.();
  assert.equal(loads, 1);
  const pending = state({ ...feed, loadingMore: true });
  const pendingHtml = markup(pending);
  assert.match(pendingHtml, /Loading more Paid posts/);
  assert.match(pendingHtml, /role="status"/);
  assert.equal(buttons(PaidDiscoveryContent({ feed: pending, t }))[0].props.disabled, true);
  assert.doesNotMatch(markup(state({ ...feed, reachedEnd: true })), /Load more/);
});

test("continuation error preserves posts and retry delegates to loadMore, not reload", () => {
  let loads = 0;
  let reloads = 0;
  const feed = state({
    items: [post("one")], reachedEnd: false, error: "Continuation failed",
    loadMore: async () => { loads += 1; }, reload: async () => { reloads += 1; },
  });
  const html = markup(feed);
  assert.match(html, /Sponsored caption one/);
  assert.match(html, /role="alert"/);
  assert.match(html, /Continuation failed/);
  assert.match(html, /Retry loading more/);
  buttons(PaidDiscoveryContent({ feed, t }))[0].props.onClick?.();
  assert.equal(loads, 1);
  assert.equal(reloads, 0);
});

test("UI actions use real Paid store pagination without constructing cursors or accounting", async () => {
  const cursor = "paid1.server.signed+/=%";
  const variables: unknown[] = [];
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body));
    assert.match(body.query, /filter: \{ algorithm: PAID \}/);
    assert.doesNotMatch(body.query, /mutation|trackPostWatch/);
    variables.push(body.variables);
    return Response.json({
      data: { feed: variables.length === 1
        ? { items: [post("one")], nextCursor: cursor }
        : { items: [post("one"), post("two")], nextCursor: null } },
    });
  };
  const store = createPaidFeedStore();
  await store.reload();
  const feed = { ...store.getSnapshot(), loadMore: store.loadMore, reload: store.reload };
  const button = buttons(PaidDiscoveryContent({ feed, t }))[0];
  button.props.onClick?.();
  await new Promise((resolve) => setImmediate(resolve));
  const html = markup({ ...store.getSnapshot(), loadMore: store.loadMore, reload: store.reload });
  assert.deepEqual(variables, [{ cursor: null, limit: 10 }, { cursor, limit: 10 }]);
  assert.equal((html.match(/<article/g) ?? []).length, 2);
  assert.doesNotMatch(html, /Load more/);
});

test("Discover retains its existing content and adds an optional separate Paid navigation action", () => {
  let navigations = 0;
  const html = renderToStaticMarkup(<TrendingSounds onBack={() => {}} />);
  assert.match(html, /Trending Sounds/);
  assert.doesNotMatch(html, /Open Paid Discovery/);
  const linked = renderToStaticMarkup(
    <TrendingSounds onBack={() => {}} onOpenPaid={() => { navigations += 1; }} />,
  );
  assert.match(linked, /Trending Sounds/);
  assert.match(linked, /aria-label="Open Paid Discovery"/);
  const link = PaidDiscoveryLink({ onOpen: () => { navigations += 1; } });
  link.props.onClick();
  assert.equal(navigations, 1);
});

test("local Paid route is separate, viewer-scoped, returns to Discover, and preserves normal feed wiring", async () => {
  const app = await readFile("src/app/App.tsx", "utf8");
  assert.match(app, /screen === "discover" && <TrendingSounds/);
  assert.match(app, /onOpenPaid=\{\(\) => setScreen\("paid"\)\}/);
  assert.match(app, /screen === "paid" && <PaidDiscoveryScreen key=\{account.email\} viewerKey=\{account.email\}/);
  assert.match(app, /onBack=\{\(\) => setScreen\("discover"\)\}/);
  assert.match(app, /useFeed\(feedTab === "following"\)/);
  assert.match(app, /useState<"forYou" \| "following">\("forYou"\)/);
  assert.doesNotMatch(app, /usePaidFeed|paidFeedItems|toDisplayVideo\(.*paid/);
  const page = await readFile("src/app/PaidDiscovery.tsx", "utf8");
  assert.doesNotMatch(page, /useFeed\(|trackPostWatch|registerCreator|noteLikeState|nextCursor|rank|dedup/);
});
