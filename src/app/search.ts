// ─── SEARCH ──────────────────────────────────────────────────────────────────
//
// One query, four kinds of answer: creators, posts, sounds and hashtags. They
// are searched together and returned together, because "who or what is this?"
// is one question to the person typing — the tabs on the results screen filter
// an answer that already exists rather than firing four different requests.
//
// Matching is deliberately forgiving: the query is normalised (case, leading @
// and #, punctuation), and a creator matches on handle, name, bio, location or
// collab status. A search that only matched handles would fail the thing people
// actually type, which is a vibe ("photographer", "berlin", "open to collab").
//
// Creators, posts and hashtags are backed by the real search API (ranked,
// paginated, filterable) when it's reachable; the in-memory directory below
// is only the offline/prototype fallback for sounds and for when the network
// request itself fails.
//
// Recent searches live in localStorage for instant, offline-safe reads, and
// are mirrored to the backend (recordSearch/searchHistory) so history survives
// a device switch for signed-in users. Trending searches come from the same
// backend aggregate, falling back to trending hashtags before anyone has
// searched for anything yet.

import { type Result } from "./auth-store";
import { CREATORS, FEED, registerCreator, type Creator, type FeedVideo } from "./creators";
import { followStateRevision, noteFollowState } from "./follow-store";
import { likeStateRevision, noteLikeState } from "./like-store";
import { SOUNDS, type Sound } from "./TrendingSounds";
import {
  clearSearchHistoryRemote, fetchSearchHistory, fetchSearchSuggestions, fetchTrendingSearches,
  recordSearch, removeSearchHistoryEntryRemote, searchHashtags, searchPosts, searchProfiles,
  type GraphQLFeedItem, type GraphQLProfileSummary, type GraphQLSearchSuggestion,
  type SearchPostFilters, type SearchProfileFilters, type SearchSortBy,
} from "./profile-graphql";

const RECENTS_KEY = "connextionz.recentSearches";
const MAX_RECENTS = 8;

const isOffline = () => typeof navigator !== "undefined" && navigator.onLine === false;

// ─── SHAPES ──────────────────────────────────────────────────────────────────


export interface HashtagResult {
  tag: string;
  /** How many posts carry it — the only number a tag row can honestly show. */
  posts: number;
  views: number;
}

export interface SearchResults {
  creators: Creator[];
  videos: FeedVideo[];
  sounds: Sound[];
  hashtags: HashtagResult[];
  /** null once the backend says there's nothing more to page in. */
  creatorsCursor: string | null;
  videosCursor: string | null;
  hashtagsCursor: string | null;
}

export const EMPTY_RESULTS: SearchResults = {
  creators: [], videos: [], sounds: [], hashtags: [],
  creatorsCursor: null, videosCursor: null, hashtagsCursor: null,
};

export const totalResults = (r: SearchResults) =>
  r.creators.length + r.videos.length + r.sounds.length + r.hashtags.length;

/** Filters the results screen can narrow a search by — one query, many facets. */
export interface SearchFilters {
  sortBy?: SearchSortBy;
  hashtag?: string;
  verifiedOnly?: boolean;
  openToCollab?: boolean;
}

// ─── NORMALISING ─────────────────────────────────────────────────────────────

const normalise = (value: string) =>
  value.trim().toLowerCase().replace(/^[@#]+/, "").replace(/[^a-z0-9\s._-]/g, "");

/** Every term has to appear somewhere, so "berlin dj" is narrower than "dj". */
const terms = (query: string) => normalise(query).split(/\s+/).filter(Boolean);

const matches = (haystack: string, words: string[]) => {
  const hay = haystack.toLowerCase();
  return words.every((word) => hay.includes(word));
};

// ─── HASHTAG INDEX ───────────────────────────────────────────────────────────

/** Built once from the feed — every tag anybody has posted, with its reach. */
const HASHTAG_INDEX: HashtagResult[] = (() => {
  const byTag = new Map<string, HashtagResult>();
  for (const video of FEED) {
    for (const tag of video.hashtags) {
      const key = tag.toLowerCase();
      const current = byTag.get(key) ?? { tag: key, posts: 0, views: 0 };
      byTag.set(key, { tag: key, posts: current.posts + 1, views: current.views + video.views });
    }
  }
  return [...byTag.values()].sort((a, b) => b.views - a.views);
})();

/** The chips shown before anything is typed. */
export const trendingHashtags = (): HashtagResult[] => HASHTAG_INDEX.slice(0, 8);

/** Creators worth suggesting on an empty query: open to collab, best scores. */
export const suggestedCreators = (limit = 6): Creator[] =>
  [...CREATORS]
    .filter((c) => c.openToCollab)
    .sort((a, b) => b.collabScore - a.collabScore || b.followers - a.followers)
    .slice(0, limit);

// ─── SEARCHING ───────────────────────────────────────────────────────────────

function run(query: string, remoteCreators?: Creator[]): SearchResults {
  const words = terms(query);
  if (!words.length) return EMPTY_RESULTS;

  const creators = (remoteCreators ?? []).filter((c) =>
    matches(`${c.username} ${c.displayName} ${c.bio} ${c.location} ${c.collabStatus}`, words),
  ).sort((a, b) => {
    // An exact handle beats a bio mention, however popular the bio's owner is.
    const exact = (c: Creator) => (normalise(c.username) === words.join(" ") ? 1 : 0);
    return exact(b) - exact(a) || b.followers - a.followers;
  });

  const sounds = SOUNDS.filter((s) =>
    matches(`${s.title} ${s.creator} ${s.genre}`, words),
  ).sort((a, b) => a.rank - b.rank);

  const hashtags = HASHTAG_INDEX.filter((h) => matches(h.tag, words));

  return { creators, videos: [], sounds, hashtags, creatorsCursor: null, videosCursor: null, hashtagsCursor: null };
}

function toCreator(profile: GraphQLProfileSummary, readRevision: number): Creator {
  const creator = registerCreator(profile);
  if (profile.isFollowing != null) noteFollowState(creator.id, profile.isFollowing, readRevision, profile.followers ?? undefined);
  return creator;
}

function toVideo(post: GraphQLFeedItem, likeRevision: number, followRevision: number): FeedVideo {
  const creator = toCreator(post.creator, followRevision);
  noteLikeState(post.id, post.isLiked ?? false, post.likes, likeRevision);
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
    isLiked: post.isLiked ?? false,
    isSaved: post.isSaved ?? false,
    isShared: post.isShared ?? false,
    mediaUrl: post.mediaUrl ?? undefined,
    durationSec: post.durationSec ?? undefined,
    hashtags: post.hashtags ?? [],
    audio: post.audio ?? "Original Sound",
    ...(post.collabWith ? { collabWith: post.collabWith } : {}),
  };
}

const toProfileFilters = (f: SearchFilters): SearchProfileFilters => ({
  verifiedOnly: f.verifiedOnly, openToCollab: f.openToCollab,
});
const toPostFilters = (f: SearchFilters): SearchPostFilters => ({
  hashtag: f.hashtag, sortBy: f.sortBy,
});

/**
 * The network seam. Latency is real enough for the screen's loading state to be
 * worth having, and an offline browser fails the way a fetch would.
 */
export async function search(query: string, filters: SearchFilters = {}): Promise<Result<SearchResults>> {
  await new Promise((r) => setTimeout(r, 320));
  if (isOffline()) {
    return { ok: false, error: "You're offline. Reconnect to search creators and posts." };
  }
  if (!normalise(query)) return { ok: true, value: EMPTY_RESULTS };

  const likeRevision = likeStateRevision();
  const followRevision = followStateRevision();
  const [profiles, posts, hashtags] = await Promise.all([
    searchProfiles(query, null, 20, toProfileFilters(filters)),
    searchPosts(query, null, 20, toPostFilters(filters)),
    searchHashtags(query, null, 20),
  ]);

  if (!posts.ok) return posts;
  const remoteCreators = profiles.ok ? profiles.value.profiles.map((profile) => toCreator(profile, followRevision)) : undefined;
  const results = run(query, remoteCreators);
  if (profiles.ok) results.creatorsCursor = profiles.value.nextCursor;
  if (posts.ok) {
    results.videos = posts.value.items.map((post) => toVideo(post, likeRevision, followRevision));
    results.videosCursor = posts.value.nextCursor;
  }
  if (hashtags.ok) {
    results.hashtags = hashtags.value.hashtags;
    results.hashtagsCursor = hashtags.value.nextCursor;
  }
  return { ok: true, value: results };
}

/** Pages in the next batch of creators for a query already on screen. */
export async function loadMoreCreators(
  query: string, cursor: string | null, filters: SearchFilters = {},
): Promise<Result<{ creators: Creator[]; nextCursor: string | null }>> {
  const readRevision = followStateRevision();
  const result = await searchProfiles(query, cursor, 20, toProfileFilters(filters));
  if (!result.ok) return result;
  return { ok: true, value: { creators: result.value.profiles.map((profile) => toCreator(profile, readRevision)), nextCursor: result.value.nextCursor } };
}

/** Pages in the next batch of posts for a query already on screen. */
export async function loadMoreVideos(
  query: string, cursor: string | null, filters: SearchFilters = {},
): Promise<Result<{ videos: FeedVideo[]; nextCursor: string | null }>> {
  const likeRevision = likeStateRevision();
  const followRevision = followStateRevision();
  const result = await searchPosts(query, cursor, 20, toPostFilters(filters));
  if (!result.ok) return result;
  return { ok: true, value: { videos: result.value.items.map((post) => toVideo(post, likeRevision, followRevision)), nextCursor: result.value.nextCursor } };
}

/** Pages in the next batch of hashtags for a query already on screen. */
export async function loadMoreHashtags(
  query: string, cursor: string | null,
): Promise<Result<{ hashtags: HashtagResult[]; nextCursor: string | null }>> {
  const result = await searchHashtags(query, cursor, 20);
  if (!result.ok) return result;
  return { ok: true, value: { hashtags: result.value.hashtags, nextCursor: result.value.nextCursor } };
}

// ─── AUTOCOMPLETE ────────────────────────────────────────────────────────────

export type Suggestion = GraphQLSearchSuggestion;

/** As-you-type suggestions: creator handles, hashtags and past searches. */
export async function fetchSuggestions(prefix: string, limit = 8): Promise<Suggestion[]> {
  const term = normalise(prefix);
  if (!term) return [];
  if (!isOffline()) {
    const remote = await fetchSearchSuggestions(prefix, limit);
    if (remote) return remote;
  }
  // Offline, or the request failed — fall back to the in-memory directory.
  const suggestions: Suggestion[] = [];
  for (const creator of CREATORS) {
    if (suggestions.length >= limit) break;
    if (creator.username.toLowerCase().startsWith(term)) {
      suggestions.push({ type: "creator", value: creator.username, label: `@${creator.username}` });
    }
  }
  for (const tag of HASHTAG_INDEX) {
    if (suggestions.length >= limit) break;
    if (tag.tag.startsWith(term)) {
      suggestions.push({ type: "hashtag", value: tag.tag, label: `#${tag.tag}` });
    }
  }
  return suggestions;
}

// ─── TRENDING SEARCHES ───────────────────────────────────────────────────────

/** What people are actually typing, aggregated server-side over the last week. */
export async function trendingSearches(limit = 8): Promise<string[]> {
  if (!isOffline()) {
    const remote = await fetchTrendingSearches(limit);
    if (remote?.length) return remote;
  }
  // Nobody has searched for anything yet (or we're offline) — hashtags fill the gap.
  return trendingHashtags().map((h) => `#${h.tag}`).slice(0, limit);
}

// ─── RECENT QUERIES ──────────────────────────────────────────────────────────

export function recentSearches(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function writeRecents(values: string[]) {
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(values.slice(0, MAX_RECENTS)));
  } catch {
    // Recents are a convenience — a full quota must not break searching.
  }
}

/**
 * Merges the signed-in viewer's server-side history into the local list, so a
 * search made on another device still shows up here. Safe to call on mount —
 * it never throws, and does nothing useful when offline or signed out.
 */
export async function syncRecentSearches(): Promise<string[]> {
  if (isOffline()) return recentSearches();
  const remote = await fetchSearchHistory(MAX_RECENTS);
  if (!remote?.length) return recentSearches();
  const remoteQueries = remote.map((entry) => entry.query);
  const merged = [
    ...remoteQueries,
    ...recentSearches().filter((v) => !remoteQueries.some((r) => r.toLowerCase() === v.toLowerCase())),
  ];
  writeRecents(merged);
  return merged.slice(0, MAX_RECENTS);
}

/** Most recent first, no duplicates. Returns the new list so state can follow. */
export function rememberSearch(query: string): string[] {
  const value = query.trim();
  if (!value) return recentSearches();
  const next = [value, ...recentSearches().filter((v) => v.toLowerCase() !== value.toLowerCase())];
  writeRecents(next);
  if (!isOffline()) void recordSearch(value);
  return next.slice(0, MAX_RECENTS);
}

export function forgetSearch(query: string): string[] {
  const next = recentSearches().filter((v) => v !== query);
  writeRecents(next);
  if (!isOffline()) void removeSearchHistoryEntryRemote(query);
  return next;
}

export function clearSearches(): string[] {
  writeRecents([]);
  if (!isOffline()) void clearSearchHistoryRemote();
  return [];
}
