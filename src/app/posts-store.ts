// ─── POSTS STORE ─────────────────────────────────────────────────────────────
//
// What the signed-in creator has published. Like `follow-store`, this is
// deliberately *not* state inside a screen: a post made in the upload sheet has
// to appear in the feed, on the profile grid and in the dashboard's content
// list at the same moment, and all three have to agree about its counters. So
// the list lives in one module store that components subscribe to.
//
// ⚠️  PROTOTYPE PERSISTENCE — localStorage, keyed per account, same caveats as
// `auth-store`. The media itself is *not* persisted: an object URL only lives as
// long as the tab, so what survives a reload is the poster and the record. That
// is the same split a real client has (blob in object storage, row in the API),
// which is why the shape is built this way from the start.
//
// ── Replacing this with a real backend ──────────────────────────────────────
// Only `uploadMedia` and `createPost` talk to the network. Swap their bodies and
// every screen is unchanged:
//
//   uploadMedia → POST   /media          (multipart, or PUT to a signed URL)
//   createPost  → POST   /posts
//   deletePost  → DELETE /posts/:id
//   load        → GET    /me/posts
//
// `uploadMedia` already reports byte progress and honours an `AbortSignal`,
// because a real upload does both and the UI is built against them.

import { useMemo, useSyncExternalStore } from "react";
import { type Result } from "./auth-store";
import { type ContentItem, type FeedVideo, OWN_POSTS } from "./creators";
import { type MediaKind, type PickedMedia, capturePoster } from "./media-upload";
import { createPost as createBackendPost, uploadPostMediaFile } from "./profile-graphql";

const POSTS_KEY = "connextionz.posts";

/** The feed id of a post the viewer made — `creatorId` on their own posts. */
export const OWN_CREATOR_ID = "me";

// ─── SHAPES ──────────────────────────────────────────────────────────────────

export type Visibility = "public" | "followers" | "private";

export const VISIBILITY_OPTIONS: { value: Visibility; label: string; hint: string }[] = [
  { value: "public", label: "Everyone", hint: "Shows in the For You feed and on your profile." },
  { value: "followers", label: "Followers", hint: "Only creators who follow you can see it." },
  { value: "private", label: "Only me", hint: "Saved to your profile, hidden from everyone else." },
];

/** A post the viewer published. A `ContentItem`, so profile grids take it as-is. */
export interface OwnPost extends ContentItem {
  kind: MediaKind;
  createdAt: number;
  hashtags: string[];
  audio: string;
  visibility: Visibility;
  allowComments: boolean;
  allowCollabs: boolean;
  durationSec: number;
  comments: number;
  shares: number;
  saves: number;
  /** Playable source. Absent after a reload — see the note at the top. */
  mediaUrl?: string;
}

/** Everything the composer collects, before an id or any counters exist. */
export interface PostDraft {
  caption: string;
  hashtags: string[];
  audio: string;
  visibility: Visibility;
  allowComments: boolean;
  allowCollabs: boolean;
  collabWith?: string;
  scheduledAt?: string;
}

export interface UploadOptions {
  /** 0 → 1, reported as bytes land. */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

// ─── STATE ───────────────────────────────────────────────────────────────────

let activeEmail: string | null = null;
let posts: OwnPost[] = [];

const listeners = new Set<() => void>();

// `useSyncExternalStore` compares snapshots by identity, so the array is rebuilt
// only when the list actually changes — never per render.
function publish() {
  posts = [...posts];
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ─── PERSISTENCE ─────────────────────────────────────────────────────────────

type PostsByAccount = Record<string, OwnPost[]>;

const key = (email: string) => email.trim().toLowerCase();

function readAll(): PostsByAccount {
  try {
    const raw = localStorage.getItem(POSTS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as PostsByAccount) : {};
  } catch {
    return {};
  }
}

/** Reports failure so a rejected write can be surfaced rather than swallowed. */
function persist(): boolean {
  if (!activeEmail) return false;
  try {
    const all = readAll();
    // The object URL is meaningless in the next tab, so it is never written.
    all[activeEmail] = posts.map(({ mediaUrl: _drop, ...rest }) => rest);
    localStorage.setItem(POSTS_KEY, JSON.stringify(all));
    return true;
  } catch {
    return false;
  }
}

// ─── ACTIVATION ──────────────────────────────────────────────────────────────

/**
 * Points the store at an account — call on sign-in, and with `null` on sign-out
 * so the next user never inherits the previous one's posts.
 */
export function activatePosts(email: string | null) {
  if (email === null) {
    activeEmail = null;
    posts = [];
    publish();
    return;
  }
  if (activeEmail === key(email)) return;
  activeEmail = key(email);
  const stored = readAll()[activeEmail];
  posts = Array.isArray(stored) ? stored : [];
  publish();
}

// ─── READS ───────────────────────────────────────────────────────────────────

export const ownPosts = (): OwnPost[] => posts;

/** Newest first — the order a profile grid and the dashboard both want. */
export function useOwnPosts(): OwnPost[] {
  return useSyncExternalStore(subscribe, ownPosts);
}

/**
 * The owner's grid: what they uploaded in this prototype, then the seeded back
 * catalogue. One list so `ProfileScreen` keeps rendering a single `ContentItem[]`.
 */
export function useOwnContent(): ContentItem[] {
  const mine = useOwnPosts();
  return mine.length ? [...mine, ...OWN_POSTS] : OWN_POSTS;
}

/** Own posts as feed slides — only the ones everyone (or a follower) can see. */
export function ownFeedVideos(): FeedVideo[] {
  return posts
    .filter((p) => p.visibility !== "private")
    .map((p) => ({
      id: p.id,
      creatorId: OWN_CREATOR_ID,
      caption: p.caption,
      hashtags: p.hashtags,
      audio: p.audio,
      thumbnail: p.thumbnail,
      views: p.views,
      likes: p.likes,
      comments: p.comments,
      shares: p.shares,
      saves: p.saves,
      ...(p.collabWith ? { collabWith: p.collabWith } : {}),
    }));
}

export function useOwnFeedVideos(): FeedVideo[] {
  const mine = useOwnPosts();
  // Memoised on the store's snapshot, so the feed's derived lists keep their
  // identity between renders and the slides do not remount for nothing.
  return useMemo(() => ownFeedVideos(), [mine]);
}

// ─── WRITES ──────────────────────────────────────────────────────────────────

/** Thrown-free stand-in for an aborted request, so callers can branch on it. */
export const UPLOAD_CANCELLED = "cancelled";

/**
 * The network seam. Resolves once the bytes are durable, reporting progress the
 * whole way — a real upload is the one place in this app where a spinner is not
 * good enough, because the wait is proportional to the file.
 */
async function uploadMedia(media: PickedMedia, options: UploadOptions): Promise<Result<string>> {
  const { onProgress, signal } = options;
  if (signal?.aborted) return { ok: false, error: UPLOAD_CANCELLED };
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return { ok: false, error: "You're offline. Reconnect and try again — your post is saved here." };
  }

  // Bigger files take longer, within reason: the point is that the bar moves at
  // a rate that matches what was picked.
  const seconds = Math.min(6, 1.2 + media.bytes / (12 * 1024 * 1024));
  const steps = 24;
  for (let i = 1; i <= steps; i++) {
    await new Promise((r) => setTimeout(r, (seconds * 1000) / steps));
    if (signal?.aborted) return { ok: false, error: UPLOAD_CANCELLED };
    onProgress?.(i / steps);
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      return { ok: false, error: "The connection dropped mid-upload. Try again — your post is saved here." };
    }
  }
  return { ok: true, value: media.url };
}

const uid = () => `me-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/**
 * Uploads the media, then creates the post. Split in two so the progress bar
 * tracks the part that actually takes time, and so a failed publish can be
 * retried without the composer losing the draft.
 */
export async function publishPost(
  media: PickedMedia,
  draft: PostDraft,
  options: UploadOptions = {},
): Promise<Result<OwnPost>> {
  if (!activeEmail) return { ok: false, error: "Sign in to publish a post." };
  if (draft.caption.trim().length > 300) {
    return { ok: false, error: "Captions are limited to 300 characters." };
  }

  const uploaded = await uploadMedia(media, options);
  if (!uploaded.ok) return uploaded;

  // The poster is captured after the upload so a cancelled publish never pays
  // for the decode. A clip that refuses to give up a frame still posts.
  const poster = await capturePoster(media, Math.min(1, media.durationSec / 3));

  const post: OwnPost = {
    id: uid(),
    kind: media.kind,
    createdAt: Date.now(),
    thumbnail: poster ?? "",
    caption: draft.caption.trim(),
    hashtags: draft.hashtags,
    audio: draft.audio.trim() || "Original Sound",
    visibility: draft.visibility,
    allowComments: draft.allowComments,
    allowCollabs: draft.allowCollabs,
    durationSec: media.durationSec,
    views: 0,
    likes: 0,
    comments: 0,
    shares: 0,
    saves: 0,
    mediaUrl: uploaded.value,
    ...(draft.scheduledAt ? { status: "scheduled", scheduledAt: draft.scheduledAt } : {}),
    ...(draft.collabWith ? { collabWith: draft.collabWith } : {}),
  };

  const backendPost = await createBackendPost({
    // The current backend creates the post before associating uploaded media.
    // These IDs remain required by the legacy GraphQL input but are ignored by
    // its resolver until media is attached through /media/posts/{postId}.
    mediaId: crypto.randomUUID(),
    thumbnailMediaId: crypto.randomUUID(),
    caption: post.caption,
    hashtags: post.hashtags,
    audio: post.audio,
    visibility: post.visibility,
    allowComments: post.allowComments,
    allowCollabs: post.allowCollabs,
    durationSec: post.durationSec,
    ...(post.status ? { status: post.status } : {}),
    ...(post.scheduledAt ? { scheduledAt: post.scheduledAt } : {}),
    ...(post.collabWith ? { collabWith: post.collabWith } : {}),
  });
  if (backendPost) {
    post.id = backendPost.id;
    post.views = backendPost.views;
    post.likes = backendPost.likes;

    const uploadedMedia = await fetch(media.url)
      .then((response) => response.blob())
      .then((blob) => uploadPostMediaFile(blob, media.name, backendPost.id));
    const uploadedPoster = poster
      ? await fetch(poster).then((response) => response.blob()).then((blob) => uploadPostMediaFile(blob, "poster.jpg", backendPost.id))
      : null;
    if (uploadedMedia) post.mediaUrl = uploadedMedia.url;
    if (uploadedPoster) post.thumbnail = uploadedPoster.url;
  }

  const previous = posts;
  posts = [post, ...posts];
  if (!persist()) {
    posts = previous;
    publish();
    return {
      ok: false,
      error: "There's no room left to save this post on this device. Free some space and try again.",
    };
  }
  publish();
  return { ok: true, value: post };
}

export function deletePost(id: string): Result<null> {
  const previous = posts;
  posts = posts.filter((p) => p.id !== id);
  if (posts.length === previous.length) return { ok: false, error: "That post no longer exists." };
  if (!persist()) {
    posts = previous;
    publish();
    return { ok: false, error: "Could not delete that just now. Try again." };
  }
  publish();
  return { ok: true, value: null };
}
