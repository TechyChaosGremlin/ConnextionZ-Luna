// ─── FOLLOW STORE ────────────────────────────────────────────────────────────
//
// Who the signed-in viewer follows. This is deliberately *not* React state in a
// screen: the same follow can be toggled from the feed rail, a profile header
// and a connections list, and all three have to agree instantly — including the
// counts derived from it. So the graph lives in one module store that components
// subscribe to, and every screen reads it through the hooks below.
//
// The backend is the source of truth. This store only holds the current
// server snapshot and optimistic state for rendering.
//
// Writes are optimistic: the UI flips first, the request runs, and a failure
// rolls the graph back and surfaces the error on the button that was pressed.
// That is the behaviour a real endpoint needs, so it is built in from the start
// rather than retrofitted once latency becomes real.

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import { getAccessToken, type Result } from "./auth-store";
import { registerCreator, type Creator, creatorById } from "./creators";
import { fetchMyFollowing, followProfile, type GraphQLFollowResult, unfollowProfile } from "./profile-graphql";

// ─── STATE ───────────────────────────────────────────────────────────────────

/** The account whose graph is loaded; null when signed out. */
let activeEmail: string | null = null;
let following = new Set<string>();
/** In-flight toggles, so every button for a creator shows the same pending state. */
let pending = new Set<string>();
/**
 * The backend's follower count for a creator, keyed by id, excluding the
 * viewer's own follow — same convention as `Creator.followers` — so it can
 * replace the seeded/stale count the instant a follow mutation resolves,
 * without waiting on a profile refetch.
 */
let followerCounts = new Map<string, number>();

const listeners = new Set<() => void>();

// `useSyncExternalStore` compares snapshots by identity, so array snapshots are
// rebuilt only when the graph actually changes — never per render.
let idsSnapshot: string[] = [];

function publish() {
  idsSnapshot = [...following];
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ─── ACTIVATION ──────────────────────────────────────────────────────────────

/**
 * Points the store at an account — call on sign-in, and with `null` on sign-out
 * so the next user never inherits the previous one's graph.
 */
export function activateFollowGraph(email: string | null) {
  if (email === null) {
    activeEmail = null;
    following = new Set();
    pending = new Set();
    followerCounts = new Map();
    publish();
    return;
  }
  if (activeEmail === email.trim().toLowerCase()) return;
  activeEmail = email.trim().toLowerCase();
  following = new Set();
  pending = new Set();
  followerCounts = new Map();
  publish();
  void fetchMyFollowing().then((profiles) => {
    if (profiles === null || activeEmail !== email.trim().toLowerCase()) return;
    following = new Set(profiles.map((profile) => registerCreator(profile).id));
    publish();
  });
}

// ─── READS ───────────────────────────────────────────────────────────────────

export const isFollowing = (creatorId: string) => following.has(creatorId);

export const followingIds = () => idsSnapshot;

/**
 * Folds a per-row `isFollowing` value from the API (search results, connections
 * lists) into the graph, so a creator seen for the first time outside the
 * initial sign-in fetch still shows the right button without waiting on a
 * separate full-list request. No-op when signed out — there is no graph to
 * merge server truth into.
 */
export function noteFollowState(creatorId: string, value: boolean) {
  if (!activeEmail || following.has(creatorId) === value) return;
  following = new Set(following);
  value ? following.add(creatorId) : following.delete(creatorId);
  publish();
}

/** Folds a follower count fresh off the wire into the graph — see `followerCounts`. */
function applyFollowerCount(creatorId: string, followersExcludingViewer: number) {
  followerCounts = new Map(followerCounts);
  followerCounts.set(creatorId, followersExcludingViewer);
  publish();
}

// ─── WRITES ──────────────────────────────────────────────────────────────────

/** The network seam. Resolves once the follow is durable. */
async function requestFollow(creatorId: string, next: boolean): Promise<Result<GraphQLFollowResult>> {
  if (!activeEmail) return { ok: false, error: "Sign in to follow creators." };
  if (!getAccessToken()) {
    const creator = creatorById(creatorId);
    return {
      ok: true,
      value: {
        following: next,
        followers: (creator?.followers ?? 0) + (next ? 1 : 0),
        followingCount: following.size,
      },
    };
  }
  const identifier = creatorById(creatorId)?.username ?? creatorId;
  return next ? followProfile(identifier) : unfollowProfile(identifier);
}

/**
 * Applies the toggle immediately, then confirms it. Returns the error to show
 * next to the button when the write failed and the graph was rolled back.
 */
export async function toggleFollow(creatorId: string): Promise<Result<boolean>> {
  if (pending.has(creatorId)) return { ok: false, error: "" };

  const previous = following.has(creatorId);
  const next = !previous;

  // Optimistic: flip the graph and mark it in flight in one publish.
  following = new Set(following);
  next ? following.add(creatorId) : following.delete(creatorId);
  pending = new Set(pending).add(creatorId);
  publish();

  const result = await requestFollow(creatorId, next);

  pending = new Set(pending);
  pending.delete(creatorId);

  if (!result.ok) {
    // Roll back to what the server still believes.
    following = new Set(following);
    previous ? following.add(creatorId) : following.delete(creatorId);
    publish();
    return result;
  }

  // The server's counts are authoritative — apply them now instead of waiting
  // on whatever refetch might happen next.
  applyFollowerCount(creatorId, result.value.followers - (result.value.following ? 1 : 0));
  publish();
  return { ok: true, value: result.value.following };
}

// ─── HOOKS ───────────────────────────────────────────────────────────────────

export function useIsFollowing(creatorId: string): boolean {
  return useSyncExternalStore(subscribe, () => following.has(creatorId));
}

export function useFollowingIds(): string[] {
  return useSyncExternalStore(subscribe, followingIds);
}

/** How many creators the viewer follows — the "Following" stat on own profile. */
export function useFollowingCount(): number {
  return useSyncExternalStore(subscribe, () => following.size);
}

/**
 * A creator's follower count with the viewer's own follow layered on top.
 * `creator.followers` excludes the viewer, so following adds exactly one and
 * the number moves the moment the button is pressed. Once a follow mutation
 * for this creator has resolved, the server's own count takes over instead.
 */
export function useFollowerCount(creator: Pick<Creator, "id" | "followers">): number {
  const followed = useIsFollowing(creator.id);
  const authoritative = useSyncExternalStore(subscribe, () => followerCounts.get(creator.id));
  return (authoritative ?? creator.followers) + (followed ? 1 : 0);
}

/**
 * Everything a follow control needs. Used by every follow affordance in the app
 * — the profile pill, the feed rail badge, the rows in a connections list — so
 * the state machine (idle → pending → confirmed / rolled back) exists once.
 */
export function useFollow(creatorId: string) {
  const followed = useIsFollowing(creatorId);
  const isPending = useSyncExternalStore(subscribe, () => pending.has(creatorId));
  const [error, setError] = useState("");

  const toggle = useCallback(async () => {
    setError("");
    const result = await toggleFollow(creatorId);
    // An empty error means "already in flight" — not something to report.
    if (!result.ok && result.error) {
      setError(result.error);
      setTimeout(() => setError(""), 2600);
    }
  }, [creatorId]);

  return { following: followed, pending: isPending, error, toggle };
}

/** The viewer's following list, as creators — drives the connections sheet. */
export function useFollowingCreators(): Creator[] {
  const ids = useFollowingIds();
  return useMemo(
    () => ids.map(creatorById).filter((c): c is Creator => !!c),
    [ids],
  );
}

