// ─── FEED STORE ──────────────────────────────────────────────────────────────
//
// The feed as something that *loads*, rather than a constant the screen renders.
// That distinction is the whole point of this file: a vertical video feed has a
// first paint with nothing in it, a failure mode with nothing in it, and an end
// the user scrolls towards — and none of those exist if the posts are an import.
//
// Pages are pulled one at a time and appended, so scrolling never waits for
// content it already has, and the next page is requested a slide *before* it is
// needed rather than at the moment the user hits the bottom.
//
// ⚠️  PROTOTYPE — pages are generated from the seeded feed rather than fetched.
//
// ── Replacing this with a real backend ──────────────────────────────────────
//   fetchFeedPage(cursor) → GET /feed?cursor=…   → { items, cursor }
// A null cursor means "no more". `useFeed` needs no changes: it already treats
// the cursor as opaque, which is what a real cursor-paginated API requires.

import { useCallback, useEffect, useRef, useState } from "react";
import { type Result } from "./auth-store";
import { FEED, registerCreator, type FeedVideo } from "./creators";
import { noteLikeState } from "./like-store";
import { noteSaveState } from "./save-store";
import { noteShareState } from "./share-store";
import { fetchFeedPageFromApi } from "./profile-graphql";

export interface FeedPage {
  items: FeedVideo[];
  /** Opaque. Null once the feed has been exhausted. */
  cursor: string | null;
}

/** The network seam. Fails the way a fetch does when the browser is offline. */
export async function fetchFeedPage(cursor: string | null, following = false): Promise<Result<FeedPage>> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return { ok: false, error: "You're offline. Reconnect to load the feed." };
  }
  const page = await fetchFeedPageFromApi(cursor, 10, following);
  if (!page && cursor === null && !following) {
    return { ok: true, value: { items: FEED, cursor: null } };
  }
  if (!page) return { ok: false, error: "The feed could not be loaded. Try again." };
  return {
    ok: true,
    value: {
      items: page.items.map((post): FeedVideo => {
        const creator = registerCreator(post.creator);
        noteLikeState(post.id, post.isLiked ?? false, post.likes);
        noteSaveState(post.id, post.isSaved ?? false, post.saves ?? 0);
        noteShareState(post.id, post.shares ?? 0, post.isShared ?? false);
        return {
          id: post.id,
          creatorId: creator.id,
          thumbnail: post.thumbnail,
          caption: post.caption,
          views: post.views,
          likes: post.likes,
          comments: post.comments ?? 0,
          shares: post.shares ?? 0,
          saves: post.saves ?? 0,
          hashtags: post.hashtags ?? [],
          audio: post.audio ?? "Original Sound",
          mediaUrl: post.mediaUrl ?? undefined,
          durationSec: post.durationSec ?? undefined,
          isLiked: post.isLiked ?? false,
          isSaved: post.isSaved ?? false,
          isShared: post.isShared ?? false,
          ...(post.collabWith ? { collabWith: post.collabWith } : {}),
        };
      }),
      cursor: page.nextCursor,
    },
  };
}

// ─── HOOK ────────────────────────────────────────────────────────────────────

export type FeedStatus = "loading" | "ready" | "error";

export interface FeedState {
  items: FeedVideo[];
  status: FeedStatus;
  error: string;
  /** A page after the first is in flight — the spinner at the end of the list. */
  loadingMore: boolean;
  /** No cursor left: the user has genuinely reached the bottom. */
  reachedEnd: boolean;
  /** Loads the next page. Safe to call on every slide change — it de-dupes. */
  loadMore: () => void;
  /** Retries after a failure, from the first page. */
  reload: () => void;
}

export function useFeed(following = false): FeedState {
  const [items, setItems] = useState<FeedVideo[]>([]);
  const [status, setStatus] = useState<FeedStatus>("loading");
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [reachedEnd, setReachedEnd] = useState(false);

  const cursor = useRef<string | null>(null);
  const requestGeneration = useRef(0);
  // One request at a time: `loadMore` is called from a scroll effect, which can
  // fire several times before a page lands.
  const inFlight = useRef(false);

  const load = useCallback(async (from: string | null, append: boolean) => {
    if (inFlight.current) return;
    const generation = requestGeneration.current;
    inFlight.current = true;
    if (append) setLoadingMore(true); else { setStatus("loading"); setError(""); }

    const result = await fetchFeedPage(from, following);
    if (generation !== requestGeneration.current) return;
    inFlight.current = false;
    setLoadingMore(false);

    if (!result.ok) {
      // A failed *first* page is an empty screen and has to say so; a failed
      // later page leaves what is already on screen alone.
      if (!append) { setError(result.error); setStatus("error"); }
      return;
    }
    cursor.current = result.value.cursor;
    setReachedEnd(result.value.cursor === null);
    setItems((previous) => {
      if (!append) return result.value.items;
      const seen = new Set(previous.map((v) => v.id));
      return [...previous, ...result.value.items.filter((v) => !seen.has(v.id))];
    });
    setStatus("ready");
  }, [following]);

  useEffect(() => {
    requestGeneration.current += 1;
    inFlight.current = false;
    cursor.current = null;
    setItems([]);
    setReachedEnd(false);
    void load(null, false);
  }, [load]);

  const loadMore = useCallback(() => {
    if (inFlight.current || reachedEnd || status !== "ready") return;
    void load(cursor.current, true);
  }, [load, reachedEnd, status]);

  const reload = useCallback(() => {
    cursor.current = null;
    setReachedEnd(false);
    void load(null, false);
  }, [load]);

  return { items, status, error, loadingMore, reachedEnd, loadMore, reload };
}
