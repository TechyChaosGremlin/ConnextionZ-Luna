// ─── LIKE STORE ──────────────────────────────────────────────────────────────
//
// Per-post like state for the signed-in viewer. Unlike follows, there is no
// "fetch all my likes" endpoint: every post already carries its own `isLiked`
// and `likes` when it arrives (a feed page, a search result), so this store is
// seeded incrementally through `noteLikeState` as posts are seen, rather than
// loaded once up front.
//
// Writes are optimistic: the heart and the count flip before the request
// resolves; a failure rolls both back onto whichever button was pressed.

import { useCallback, useState, useSyncExternalStore } from "react";
import { getAccessToken, type Result } from "./auth-store";
import { likePost, unlikePost } from "./profile-graphql";

let liked = new Map<string, boolean>();
let counts = new Map<string, number>();
let pending = new Set<string>();
let activeEmail: string | null = null;

const listeners = new Set<() => void>();

function publish() {
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function activateLikeGraph(email: string | null) {
  const normalizedEmail = email?.trim().toLowerCase() ?? null;
  if (activeEmail === normalizedEmail) return;
  activeEmail = normalizedEmail;
  liked = new Map();
  counts = new Map();
  pending = new Set();
  publish();
}

/**
 * Folds a post's server-known like state into the store. A no-op once the
 * post has been seen before, so a later page or refetch can never clobber an
 * in-flight or already-resolved toggle with a stale snapshot.
 */
export function noteLikeState(postId: string, isLiked: boolean, likes: number) {
  if (liked.has(postId)) return;
  liked = new Map(liked).set(postId, isLiked);
  counts = new Map(counts).set(postId, likes);
  publish();
}

/** The network seam. Resolves once the like/unlike is durable. */
async function requestLike(postId: string, next: boolean, optimisticCount: number): Promise<Result<{ liked: boolean; likes: number }>> {
  if (!getAccessToken()) {
    return { ok: true, value: { liked: next, likes: optimisticCount } };
  }
  return next ? likePost(postId) : unlikePost(postId);
}

/**
 * Applies the toggle immediately, then confirms it. Returns the error to show
 * next to the button when the write failed and the state was rolled back.
 */
export async function toggleLike(postId: string): Promise<Result<boolean>> {
  if (pending.has(postId)) return { ok: false, error: "" };

  const previousLiked = liked.get(postId) ?? false;
  const previousCount = counts.get(postId) ?? 0;
  const next = !previousLiked;

  liked = new Map(liked).set(postId, next);
  counts = new Map(counts).set(postId, Math.max(0, previousCount + (next ? 1 : -1)));
  pending = new Set(pending).add(postId);
  publish();

  const result = await requestLike(postId, next, Math.max(0, previousCount + (next ? 1 : -1)));

  pending = new Set(pending);
  pending.delete(postId);

  if (!result.ok) {
    liked = new Map(liked).set(postId, previousLiked);
    counts = new Map(counts).set(postId, previousCount);
    publish();
    return result;
  }

  // The server's own numbers are authoritative — apply them now instead of
  // trusting the optimistic +/-1.
  liked = new Map(liked).set(postId, result.value.liked);
  counts = new Map(counts).set(postId, result.value.likes);
  publish();
  return { ok: true, value: result.value.liked };
}

/**
 * Everything a like button needs. `fallbackLiked`/`fallbackLikes` are the
 * values carried on the post itself, used until (and unless) the store has
 * been seeded or the viewer has toggled it.
 */
export function useLike(postId: string, fallbackLiked: boolean, fallbackLikes: number) {
  const isLiked = useSyncExternalStore(subscribe, () => liked.get(postId) ?? fallbackLiked);
  const likeCount = useSyncExternalStore(subscribe, () => counts.get(postId) ?? fallbackLikes);
  const isPending = useSyncExternalStore(subscribe, () => pending.has(postId));
  const [error, setError] = useState("");

  const toggle = useCallback(async () => {
    setError("");
    const result = await toggleLike(postId);
    // An empty error means "already in flight" — not something to report.
    if (!result.ok && result.error) {
      setError(result.error);
      setTimeout(() => setError(""), 2600);
    }
  }, [postId]);

  return { liked: isLiked, likes: likeCount, pending: isPending, error, toggle };
}
