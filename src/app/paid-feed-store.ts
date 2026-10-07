import { useEffect, useMemo, useSyncExternalStore } from "react";
import { fetchPaidFeedPage, type PaidFeedItem } from "./paid-feed-graphql.ts";

export interface PaidFeedSnapshot {
  items: PaidFeedItem[];
  status: "loading" | "ready" | "error";
  error: string;
  loadingMore: boolean;
  reachedEnd: boolean;
}

/** Instance-local Paid state; never registers posts, creators, or engagement in normal stores. */
export function createPaidFeedStore(limit = 10) {
  let state: PaidFeedSnapshot = {
    items: [],
    status: "loading",
    error: "",
    loadingMore: false,
    reachedEnd: false,
  };
  let cursor: string | null = null;
  let generation = 0;
  let inFlight = false;
  const listeners = new Set<() => void>();

  const publish = (patch: Partial<PaidFeedSnapshot>) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };

  const load = async (append: boolean): Promise<void> => {
    if (inFlight) return;
    const requestGeneration = generation;
    inFlight = true;
    publish(append
      ? { loadingMore: true, error: "" }
      : { status: "loading", error: "" });
    const result = await fetchPaidFeedPage(append ? cursor : null, limit);
    if (requestGeneration !== generation) return;
    inFlight = false;
    if (!result.ok) {
      publish({
        loadingMore: false,
        status: append ? "ready" : "error",
        error: result.error,
      });
      return;
    }
    cursor = result.value.nextCursor;
    const items = append ? [...state.items] : [];
    const posts = new Set(items.map((item) => item.id));
    const campaigns = new Set(
      items.flatMap((item) => item.paidCampaignId === null ? [] : [item.paidCampaignId]),
    );
    for (const item of result.value.items) {
      if (posts.has(item.id) || (item.paidCampaignId !== null && campaigns.has(item.paidCampaignId))) {
        continue;
      }
      items.push(item);
      posts.add(item.id);
      if (item.paidCampaignId !== null) campaigns.add(item.paidCampaignId);
    }
    publish({
      items,
      status: "ready",
      error: "",
      loadingMore: false,
      reachedEnd: cursor === null,
    });
  };

  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    reload: () => {
      generation += 1;
      inFlight = false;
      cursor = null;
      publish({ items: [], reachedEnd: false, loadingMore: false });
      return load(false);
    },
    loadMore: () => {
      if (inFlight || state.reachedEnd || state.status !== "ready") return Promise.resolve();
      return load(true);
    },
  };
}

export type PaidFeedState = PaidFeedSnapshot & {
  loadMore: () => Promise<void>;
  reload: () => Promise<void>;
};

/** The future dedicated area supplies an account key to discard state on viewer changes. */
export function usePaidFeed(viewerKey: string, limit = 10): PaidFeedState {
  const store = useMemo(() => createPaidFeedStore(limit), [viewerKey, limit]);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => { void store.reload(); }, [store]);
  return { ...state, loadMore: store.loadMore, reload: store.reload };
}
