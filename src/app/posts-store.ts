// ─── POSTS STORE ─────────────────────────────────────────────────────────────
//
// What the signed-in creator has published. Like `follow-store`, this is
// deliberately *not* state inside a screen: a post made in the upload sheet has
// to appear in the feed, on the profile grid and in the dashboard's content
// list at the same moment, and all three have to agree about its counters. So
// the list lives in one module store that components subscribe to.
//
// The API is authoritative. localStorage only caches completed posts and stable
// media references; private storage download URLs are resolved at render time.

import { useMemo, useSyncExternalStore } from "react";
import { type Result } from "./auth-store";
import { type ContentItem, type FeedVideo, OWN_POSTS } from "./creators";
import { type MediaKind, type PickedMedia, capturePoster } from "./media-upload";
import { createPost as createBackendPost, updatePost, uploadPostMediaFile, type UploadedPostMedia } from "./profile-graphql";

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
  /** Durable API media reference, not a blob or an expiring signed URL. */
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
  /** 0 → 1 across draft creation, durable uploads, and publication. */
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
    all[activeEmail] = posts;
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
      mediaUrl: p.mediaUrl,
      mediaType: p.mediaType,
      durationSec: p.durationSec,
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

const pendingDrafts = new WeakMap<PickedMedia, {
  email: string; id: string; primaryId: string; thumbnailId: string;
  primary?: UploadedPostMedia; thumbnail?: UploadedPostMedia;
}>();

/**
 * Creates a draft, attaches durable media, then publishes the same post ID.
 * Failed uploads remain drafts and can be retried without losing the file.
 */
export async function publishPost(
  media: PickedMedia,
  draft: PostDraft,
  options: UploadOptions = {},
): Promise<Result<OwnPost>> {
  if (!activeEmail) return { ok: false, error: "Sign in to publish a post." };
  const email = activeEmail;
  if (draft.caption.trim().length > 300) {
    return { ok: false, error: "Captions are limited to 300 characters." };
  }

  if (options.signal?.aborted) return { ok: false, error: UPLOAD_CANCELLED };
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return { ok: false, error: "You're offline. Reconnect and try again." };
  }
  const poster = media.kind === "video"
    ? await capturePoster(media, Math.min(1, media.durationSec / 3))
    : null;
  if (media.kind === "video" && !poster) {
    return { ok: false, error: "Could not create a video thumbnail. Pick the file again and retry." };
  }
  const input = {
    caption: draft.caption.trim(),
    hashtags: draft.hashtags,
    audio: draft.audio.trim() || "Original Sound",
    visibility: draft.visibility,
    allowComments: draft.allowComments,
    allowCollabs: draft.allowCollabs,
    durationSec: media.durationSec,
    status: draft.scheduledAt ? "scheduled" : "published",
    ...(draft.scheduledAt ? { scheduledAt: draft.scheduledAt } : {}),
    ...(draft.collabWith ? { collabWith: draft.collabWith } : {}),
  };

  let pending = pendingDrafts.get(media);
  if (!pending || pending.email !== email) {
    const created = await createBackendPost({ ...input, status: "draft" });
    if (!created.ok) return created;
    pending = {
      email, id: created.value.id,
      primaryId: crypto.randomUUID(), thumbnailId: crypto.randomUUID(),
    };
    pendingDrafts.set(media, pending);
  }
  if (options.signal?.aborted) return { ok: false, error: UPLOAD_CANCELLED };
  options.onProgress?.(0.1);
  if (!pending.primary) {
    const uploaded = await uploadPostMediaFile(
      media.file, media.name, pending.id, "primary", options.signal, false, undefined, pending.primaryId,
    );
    if (!uploaded.ok) return uploaded;
    pending.primary = uploaded.value;
  }
  options.onProgress?.(0.7);
  if (media.kind === "video" && poster && !pending.thumbnail) {
    try {
      const response = await fetch(poster, { signal: options.signal });
      if (!response.ok) return { ok: false, error: "Could not read the video thumbnail." };
      const thumbnail = await uploadPostMediaFile(
        await response.blob(), "poster.jpg", pending.id, "thumbnail", options.signal,
        false, pending.primary.id, pending.thumbnailId,
      );
      if (!thumbnail.ok) return thumbnail;
      pending.thumbnail = thumbnail.value;
    } catch (error) {
      if (options.signal?.aborted) return { ok: false, error: UPLOAD_CANCELLED };
      console.warn("Video thumbnail upload failed", error);
      return { ok: false, error: "Could not upload the video thumbnail. Try again." };
    }
  }
  if (options.signal?.aborted) return { ok: false, error: UPLOAD_CANCELLED };
  const completed = await updatePost(pending.id, {
    ...input, mediaId: pending.primary.id, thumbnailMediaId: pending.thumbnail?.id ?? pending.primary.id,
  });
  if (!completed.ok) return completed;
  if (completed.value.mediaUrl !== pending.primary.url ||
      completed.value.thumbnail !== (pending.thumbnail?.url ?? pending.primary.url)) {
    return { ok: false, error: "The server did not confirm the post's media. Reload your profile before retrying." };
  }
  pendingDrafts.delete(media);
  const post: OwnPost = {
    ...input,
    id: completed.value.id,
    thumbnail: completed.value.thumbnail,
    views: completed.value.views,
    likes: completed.value.likes,
    kind: media.kind,
    mediaType: pending.primary.contentType,
    mediaUrl: completed.value.mediaUrl,
    status: draft.scheduledAt ? "scheduled" : "published",
    createdAt: Date.now(),
    comments: completed.value.comments ?? 0,
    shares: completed.value.shares ?? 0,
    saves: completed.value.saves ?? 0,
  };
  if (activeEmail !== email) {
    return { ok: false, error: "Your account changed while publishing. Reload the original account's profile to view the post." };
  }
  posts = [post, ...posts.filter((existing) => existing.id !== post.id)];
  if (!persist()) {
    console.warn("Post published, but its local cache could not be saved. The API retains the post.");
  }
  publish();
  options.onProgress?.(1);
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
