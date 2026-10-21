import { useEffect, useRef, useState } from "react";
import { ACCEPTED_VIDEO_TYPES, readMediaFile, revokeMedia, type PickedMedia } from "./media-upload";
import { canRepairPost, repairPostMedia, validateRepairFile, type RepairProgress } from "./post-media-repair";
import type { GraphQLPost } from "./profile-graphql";
import { PostMedia } from "./post-media";
import { ACCENT, useTokens } from "./settings-ui";
import { useTheme } from "./ThemeContext";

export function PostMediaRepair({ posts, onClose }: {
  posts: GraphQLPost[];
  onClose: () => void;
}) {
  const t = useTokens(useTheme());
  const candidates = posts.filter(canRepairPost);
  const [postId, setPostId] = useState(candidates[0]?.id ?? "");
  const post = candidates.find((item) => item.id === postId);
  const [video, setVideo] = useState<PickedMedia | null>(null);
  const [poster, setPoster] = useState<PickedMedia | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [started, setStarted] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const progress = useRef<RepairProgress>({});
  const mounted = useRef(true);
  const picking = useRef(false);
  const submitting = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => () => revokeMedia(video), [video]);
  useEffect(() => () => revokeMedia(poster), [poster]);

  async function pick(file: File | undefined, role: "primary" | "thumbnail") {
    if (!file || picking.current || submitting.current) return;
    setError("");
    const check = validateRepairFile(file, role);
    if (!check.ok) { setError(check.error); return; }
    picking.current = true;
    setReading(true);
    const result = await readMediaFile(file);
    picking.current = false;
    if (!mounted.current) {
      if (result.ok) revokeMedia(result.value);
      return;
    }
    setReading(false);
    setConfirmed(false);
    if (!result.ok) { setError(result.error); return; }
    if (role === "primary") setVideo(result.value);
    else setPoster(result.value);
  }

  async function attach() {
    if (!post || !poster || !confirmed || submitting.current || picking.current) return;
    submitting.current = true;
    setBusy(true);
    setStarted(true);
    setError("");
    const result = await repairPostMedia(post, video?.file ?? null, poster.file, progress.current);
    submitting.current = false;
    if (!mounted.current) return;
    setBusy(false);
    if (!result.ok) setError(result.error);
    else setDone(true);
  }

  const attachedVideo = progress.current.primaryUrl ?? post?.mediaUrl;
  return (
    <div role="dialog" aria-modal="true" aria-label="Repair missing post media"
      className="absolute inset-0 z-50 overflow-y-auto p-5 pt-14" style={{ background: t.bg, color: t.body }}>
      <h1 className="text-xl font-bold mb-3" style={{ color: t.heading }}>Repair missing media</h1>
      <p className="text-sm mb-4">
        Uploads new copies to the existing post, not a restoration of an original storage object.
        The post ID, caption, reactions, comments, visibility and other metadata stay unchanged.
        Video and thumbnail uploads are saved separately; a partial repair stays on the post.
      </p>
      {done ? <p role="status">Media attached and verified on your existing post.</p> : (
        <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void attach(); }}>
          <label className="block">Existing post
            <select value={postId} disabled={started || busy || reading} className="block w-full p-2 bg-transparent border rounded"
              onChange={(event) => { setPostId(event.target.value); setConfirmed(false); setError(""); }}>
              {candidates.map((item) => <option key={item.id} value={item.id}>
                {item.caption || "Untitled post"} ({item.id})
              </option>)}
            </select>
          </label>
          {post && <p className="text-sm break-all">Post ID: {post.id}</p>}
          {attachedVideo
            ? <><p>Video is already attached. Only its missing thumbnail will be uploaded.</p>
                <PostMedia src={attachedVideo} mediaType="video" alt="Attached post video" controls className="w-full max-h-52" /></>
            : <label className="block">Video (up to 512MB)
                <input type="file" accept={ACCEPTED_VIDEO_TYPES.join(",")} disabled={busy || reading || started}
                  onChange={(event) => { void pick(event.target.files?.[0], "primary"); event.target.value = ""; }} />
              </label>}
          {!attachedVideo && video && <video src={video.url} aria-label="Selected video preview" controls className="w-full max-h-52" />}
          <label className="block">JPEG thumbnail (up to 8MB)
            <input type="file" accept="image/jpeg" disabled={busy || reading || started}
              onChange={(event) => { void pick(event.target.files?.[0], "thumbnail"); event.target.value = ""; }} />
          </label>
          {poster && <img src={poster.url} alt="Selected JPEG thumbnail preview" className="w-full max-h-52 object-contain" />}
          <label className="flex gap-2">
            <input type="checkbox" checked={confirmed} disabled={busy || reading}
              onChange={(event) => setConfirmed(event.target.checked)} />
            I confirm these files belong to this existing post and want to attach them.
          </label>
          {error && <p role="alert">{error}</p>}
          {reading && <p role="status">Validating preview...</p>}
          {busy && <p role="status">Attaching media. Keep this screen open...</p>}
          <button type="submit" disabled={!post || !poster || (!attachedVideo && !video) || !confirmed || busy || reading}
            className="rounded-full px-4 py-2 font-bold disabled:opacity-40" style={{ background: ACCENT, color: "#fff" }}>
            {started ? "Retry repair / verification" : "Confirm and attach media"}
          </button>
        </form>
      )}
      <button type="button" onClick={onClose} disabled={busy || reading} className="mt-5 px-4 py-2">
        {done ? "Done" : "Close and reload profile"}
      </button>
    </div>
  );
}
