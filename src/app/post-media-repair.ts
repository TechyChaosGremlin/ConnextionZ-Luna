import type { Result } from "./auth-store";
import { ACCEPTED_VIDEO_TYPES, validateMediaFile } from "./media-upload";
import { fetchMeProfile, uploadPostMediaFile, type GraphQLPost } from "./profile-graphql";

export type RepairProgress = { primaryUrl?: string; thumbnailUrl?: string };

export function canRepairPost(post: GraphQLPost): boolean {
  return !post.thumbnail && (
    post.mediaType === "video" || !!post.mediaType?.startsWith("video/")
  );
}

export function validateRepairFile(file: File, role: "primary" | "thumbnail"): Result<File> {
  const valid = validateMediaFile(file);
  if (!valid.ok) return valid;
  if (role === "primary" && !(ACCEPTED_VIDEO_TYPES as readonly string[]).includes(file.type)) {
    return { ok: false, error: "Choose a supported video file." };
  }
  if (role === "thumbnail" && (file.type !== "image/jpeg" || file.size > 8 * 1024 * 1024)) {
    return { ok: false, error: "Choose a JPEG thumbnail no larger than 8MB." };
  }
  return valid;
}

export async function repairPostMedia(
  post: GraphQLPost, video: File | null, poster: File, progress: RepairProgress,
): Promise<Result<GraphQLPost>> {
  const posterCheck = validateRepairFile(poster, "thumbnail");
  if (!posterCheck.ok) return posterCheck;
  if (!post.mediaUrl && !progress.primaryUrl) {
    if (!video) return { ok: false, error: "Choose a video for this post." };
    const videoCheck = validateRepairFile(video, "primary");
    if (!videoCheck.ok) return videoCheck;
  }

  const profile = await fetchMeProfile();
  const current = profile?.posts?.find((item) => item.id === post.id);
  if (!current) return { ok: false, error: "Could not verify this post on your own profile. Reload before retrying." };
  const expectedPrimary = progress.primaryUrl ?? post.mediaUrl;
  if ((current.mediaUrl || null) !== (expectedPrimary || null)) {
    return { ok: false, error: "The post's video changed or an upload may have completed. Close and reopen repair to review it." };
  }
  if (progress.thumbnailUrl && current.thumbnail === progress.thumbnailUrl) return { ok: true, value: current };
  if (!canRepairPost(current)) {
    return { ok: false, error: "This post no longer has missing video media. Reload your profile to review it." };
  }
  if (!current.mediaUrl && video) {
    const uploaded = await uploadPostMediaFile(video, video.name, post.id, "primary", undefined, true);
    if (!uploaded.ok) return {
      ok: false, error: `Video upload was not confirmed: ${uploaded.error} No thumbnail was sent. Retry checks the post first.`,
    };
    progress.primaryUrl = uploaded.value.url;
  }
  if (!progress.thumbnailUrl) {
    const primaryUrl = progress.primaryUrl ?? post.mediaUrl;
    const primaryId = primaryUrl?.match(/\/media\/([0-9a-f-]+)\/content$/i)?.[1];
    if (!primaryId) return {
      ok: false, error: "Could not identify the confirmed video. Reload before repairing its thumbnail.",
    };
    const uploaded = await uploadPostMediaFile(
      poster, poster.name, post.id, "thumbnail", undefined, true, primaryId,
    );
    if (!uploaded.ok) return {
      ok: false, error: `Video is attached, but thumbnail upload was not confirmed: ${uploaded.error} Retry sends only the thumbnail. If it already exists, reload to review it.`,
    };
    progress.thumbnailUrl = uploaded.value.url;
  }
  const refreshed = (await fetchMeProfile())?.posts?.find((item) => item.id === post.id);
  if (!refreshed || refreshed.mediaUrl !== (progress.primaryUrl ?? post.mediaUrl) ||
      refreshed.thumbnail !== progress.thumbnailUrl) {
    return { ok: false, error: "Uploads were confirmed, but the refreshed post could not be verified. Retry verification or reload; do not upload again." };
  }
  return { ok: true, value: refreshed };
}
