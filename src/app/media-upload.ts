// ─── MEDIA UPLOAD ────────────────────────────────────────────────────────────
//
// Turning a video or photo the user picked into something the app can preview,
// post and store. Same three-step shape as `avatar-upload.ts`, and for the same
// reason — the UI has to be able to stop between the steps:
//
//   validateMediaFile()  →  reject the wrong type or an oversized file *before*
//                           reading hundreds of megabytes into memory
//   readMediaFile()      →  decode it, so a file that only claims to be a video
//                           is caught, and produce the preview source
//   capturePoster()      →  grab a frame as the post's thumbnail, which is what
//                           the feed and every profile grid actually render
//
// The original file and generated poster are uploaded by `publishPost`.
// Object URLs are only composer previews, never durable post references.

import { type Result } from "./auth-store";

// ─── LIMITS ──────────────────────────────────────────────────────────────────

export const ACCEPTED_VIDEO_TYPES = ["video/mp4", "video/webm", "video/quicktime", "video/ogg"] as const;
export const ACCEPTED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

/** For the file input's `accept`, and the hint under the drop zone. */
export const ACCEPT_ATTRIBUTE = [...ACCEPTED_VIDEO_TYPES, ...ACCEPTED_IMAGE_TYPES].join(",");
export const ACCEPTED_LABEL = "MP4, WebM, MOV, JPG or PNG";

export const MAX_BYTES = 512 * 1024 * 1024;
export const MAX_LABEL = "512MB";

/** Longer than this is a different product. Trim before uploading. */
export const MAX_DURATION_SECONDS = 10 * 60;

/** Below this a post looks soft full-screen, so it is rejected. */
export const MIN_DIMENSION = 240;

/** Posters are stored, so they are downscaled — 720 is sharp at every tile. */
export const POSTER_WIDTH = 720;

export type MediaKind = "video" | "image";

export interface PickedMedia {
  file: File;
  kind: MediaKind;
  /** Object URL — playable/renderable for the life of the tab. */
  url: string;
  width: number;
  height: number;
  /** 0 for a still. */
  durationSec: number;
  name: string;
  bytes: number;
  type: string;
}

// ─── VALIDATION ──────────────────────────────────────────────────────────────

const mb = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(1)}MB`;

export const kindOf = (type: string): MediaKind | null =>
  (ACCEPTED_VIDEO_TYPES as readonly string[]).includes(type) ? "video"
  : (ACCEPTED_IMAGE_TYPES as readonly string[]).includes(type) ? "image"
  : null;

/** Cheap checks first: type and size, straight off the file handle. */
export function validateMediaFile(file: File): Result<File> {
  if (!kindOf(file.type)) {
    // A file with no type at all reads as "" — name the accepted set instead of
    // echoing something unhelpful back at the user.
    return { ok: false, error: `That file is not a video or photo we can post. Pick a ${ACCEPTED_LABEL} file.` };
  }
  if (file.size > MAX_BYTES) {
    return { ok: false, error: `That file is ${mb(file.size)}. The limit is ${MAX_LABEL}.` };
  }
  if (file.size === 0) {
    return { ok: false, error: "That file is empty. Pick another one." };
  }
  return { ok: true, value: file };
}

// ─── READING ─────────────────────────────────────────────────────────────────

function decodeVideo(url: string): Promise<HTMLVideoElement | null> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    // Some browsers only report dimensions once a frame is available, so both
    // events resolve — whichever lands first has what we need.
    video.onloadedmetadata = () => resolve(video);
    video.onerror = () => resolve(null);
    video.src = url;
  });
}

function decodeImage(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

/**
 * Validates, then decodes. Decoding is the real check — a `.zip` renamed to
 * `.mp4` passes the type check on some platforms but never decodes. The object
 * URL is revoked on every failure path so a rejected pick leaks nothing.
 */
export async function readMediaFile(file: File): Promise<Result<PickedMedia>> {
  const valid = validateMediaFile(file);
  if (!valid.ok) return valid;

  const kind = kindOf(file.type)!;
  const url = URL.createObjectURL(file);
  const fail = (error: string): Result<PickedMedia> => {
    URL.revokeObjectURL(url);
    return { ok: false, error };
  };

  if (kind === "image") {
    const img = await decodeImage(url);
    if (!img) return fail("That file is not a valid image.");
    if (img.naturalWidth < MIN_DIMENSION || img.naturalHeight < MIN_DIMENSION) {
      return fail(`That image is ${img.naturalWidth}×${img.naturalHeight}. Use one at least ${MIN_DIMENSION}×${MIN_DIMENSION}.`);
    }
    return {
      ok: true,
      value: {
        file, kind, url, width: img.naturalWidth, height: img.naturalHeight,
        durationSec: 0, name: file.name, bytes: file.size, type: file.type,
      },
    };
  }

  const video = await decodeVideo(url);
  if (!video) return fail("That video could not be read. It may use a codec this browser cannot play.");

  const duration = Number.isFinite(video.duration) ? video.duration : 0;
  if (duration > MAX_DURATION_SECONDS) {
    return fail(`That video is ${formatDuration(duration)}. The limit is ${formatDuration(MAX_DURATION_SECONDS)}.`);
  }
  if (video.videoWidth && video.videoWidth < MIN_DIMENSION) {
    return fail(`That video is ${video.videoWidth}×${video.videoHeight}. Use one at least ${MIN_DIMENSION}px wide.`);
  }

  return {
    ok: true,
    value: {
      file, kind, url,
      width: video.videoWidth || 0,
      height: video.videoHeight || 0,
      durationSec: duration,
      name: file.name, bytes: file.size, type: file.type,
    },
  };
}

/** Frees the preview. Call when the pick is replaced, cancelled or published. */
export const revokeMedia = (media: PickedMedia | null) => {
  if (media) URL.revokeObjectURL(media.url);
};

// ─── POSTER ──────────────────────────────────────────────────────────────────

function seekTo(video: HTMLVideoElement, seconds: number): Promise<boolean> {
  return new Promise((resolve) => {
    // A seek that never fires would hang the publish, so it is bounded.
    const done = (ok: boolean) => { clearTimeout(timer); resolve(ok); };
    const timer = setTimeout(() => done(false), 3000);
    video.onseeked = () => done(true);
    video.onerror = () => done(false);
    try { video.currentTime = seconds; } catch { done(false); }
  });
}

/**
 * The post's thumbnail: a frame from `atSeconds` (a still just re-encodes),
 * scaled to `POSTER_WIDTH` and encoded as JPEG. This is what gets persisted and
 * what every grid tile renders, so it is deliberately small.
 *
 * Returns null if a frame cannot be captured; video publishing then reports
 * an error rather than leaving an empty profile tile.
 */
export async function capturePoster(media: PickedMedia, atSeconds = 0.1): Promise<string | null> {
  const source = media.kind === "image"
    ? await decodeImage(media.url)
    : await (async () => {
        const video = await decodeVideo(media.url);
        if (!video) return null;
        const at = Math.min(Math.max(atSeconds, 0), Math.max(media.durationSec - 0.05, 0));
        // A zero-length or un-seekable clip still gives frame zero.
        if (at > 0) await seekTo(video, at);
        return video;
      })();

  if (!source) return null;

  const sw = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
  const sh = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
  if (!sw || !sh) return null;

  const width = Math.min(POSTER_WIDTH, sw);
  const height = Math.round((sh / sw) * width);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  ctx.imageSmoothingQuality = "high";
  try {
    ctx.drawImage(source, 0, 0, width, height);
    return canvas.toDataURL("image/jpeg", 0.82);
  } catch {
    // A tainted canvas cannot be read back. Never true for an object URL, but
    // the poster is optional, so this degrades instead of throwing.
    return null;
  }
}

// ─── FORMATTING ──────────────────────────────────────────────────────────────

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export const formatBytes = (bytes: number) =>
  bytes >= 1024 * 1024 ? mb(bytes) : `${Math.max(1, Math.round(bytes / 1024))}KB`;

/** Splits "#one #two, three" into normalised, de-duplicated tags. */
export function parseHashtags(input: string): string[] {
  const seen = new Set<string>();
  return input
    .split(/[\s,]+/)
    .map((raw) => raw.replace(/^#+/, "").replace(/[^a-zA-Z0-9_]/g, "").toLowerCase())
    .filter((tag) => tag.length > 0 && tag.length <= 30)
    .filter((tag) => (seen.has(tag) ? false : (seen.add(tag), true)))
    .slice(0, 10)
    .map((tag) => `#${tag}`);
}
