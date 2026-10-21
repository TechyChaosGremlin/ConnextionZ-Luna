// ─── UPLOAD ──────────────────────────────────────────────────────────────────
//
// Publishing a post, as four states rather than one form: pick → compose →
// uploading → published. They are separate because the user has a different
// question at each one ("what am I posting?", "what does it say?", "how long
// will this take?", "where did it go?"), and because a failure at any of them
// has to be recoverable without losing the draft.
//
// The picked file never becomes a post on its own — `media-upload.ts` validates
// and decodes it, `posts-store.ts` uploads it and creates the record. This
// screen only owns the draft and the state machine, so both of those can be
// pointed at a real API without touching what the user sees.

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  AlertCircle, Check, Clock, Film, Hash, Image as ImageIcon, Music, Sparkles,
  Trash2, Upload as UploadIcon, Users, X,
} from "lucide-react";
import { useTheme } from "./ThemeContext";
import {
  ACCENT, Callout, Chip, Field, PrimaryAction, SecondaryAction, useTokens,
} from "./settings-ui";
import {
  ACCEPTED_LABEL, ACCEPT_ATTRIBUTE, MAX_LABEL, type PickedMedia,
  formatBytes, formatDuration, parseHashtags, readMediaFile, revokeMedia,
} from "./media-upload";
import {
  type OwnPost, type PostDraft, type Visibility, UPLOAD_CANCELLED,
  VISIBILITY_OPTIONS, publishPost,
} from "./posts-store";
import { CREATORS } from "./creators";
import { useFollowingCreators } from "./follow-store";
import { Avatar, Thumb } from "./profile-ui";

type Stage = "pick" | "compose" | "uploading" | "published";

const SOUND_SUGGESTIONS = [
  "Original Sound", "Late Night Loop", "golden hour — JVKE",
  "HYPERSONIC — nova.dj", "lo-fi beats — study playlist",
];

export function UploadScreen({
  onClose, onPublished, onViewPost,
}: {
  onClose: () => void;
  /** Lets the app fold the new post into the feed it is already showing. */
  onPublished?: (post: OwnPost) => void;
  onViewPost?: (postId: string) => void;
}) {
  const isDark = useTheme();
  const t = useTokens(isDark);

  const [stage, setStage] = useState<Stage>("pick");
  const [media, setMedia] = useState<PickedMedia | null>(null);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  const [caption, setCaption] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [audio, setAudio] = useState("Original Sound");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [allowComments, setAllowComments] = useState(true);
  const [allowCollabs, setAllowCollabs] = useState(true);
  const [collabWith, setCollabWith] = useState<string>("");
  const [scheduledAt, setScheduledAt] = useState("");

  const [progress, setProgress] = useState(0);
  const [published, setPublished] = useState<OwnPost | null>(null);

  const fileRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const hashtags = parseHashtags(tagInput);

  useEffect(() => () => {
    abortRef.current?.abort();
    revokeMedia(media);
  }, [media]);

  // ── Picking ───────────────────────────────────────────────────────────────

  const acceptFile = useCallback(async (file: File | undefined | null) => {
    if (!file) return;
    setError("");
    setReading(true);
    const result = await readMediaFile(file);
    setReading(false);
    if (!result.ok) { setError(result.error); return; }
    // Replacing a pick frees the one it replaces.
    setMedia((previous) => { revokeMedia(previous); return result.value; });
    setStage("compose");
  }, []);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void acceptFile(e.dataTransfer.files?.[0]);
  };

  const clearMedia = () => {
    revokeMedia(media);
    setMedia(null);
    setStage("pick");
    setProgress(0);
    setError("");
  };

  // ── Publishing ────────────────────────────────────────────────────────────

  const draft = (): PostDraft => ({
    caption,
    hashtags,
    audio,
    visibility,
    allowComments,
    allowCollabs,
    ...(collabWith ? { collabWith } : {}),
    ...(scheduledAt ? { scheduledAt: new Date(scheduledAt).toISOString() } : {}),
  });

  const publish = async () => {
    if (!media) return;
    setError("");
    setProgress(0);
    setStage("uploading");
    const controller = new AbortController();
    abortRef.current = controller;

    const result = await publishPost(media, draft(), {
      onProgress: setProgress,
      signal: controller.signal,
    });
    abortRef.current = null;

    if (!result.ok) {
      // A cancel is the user's own doing — return them to the draft silently.
      setError(result.error === UPLOAD_CANCELLED ? "" : result.error);
      setStage("compose");
      return;
    }
    setPublished(result.value);
    setStage("published");
    onPublished?.(result.value);
  };

  const cancelUpload = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const postAnother = () => {
    setMedia(null);
    setPublished(null);
    setCaption("");
    setTagInput("");
    setAudio("Original Sound");
    setCollabWith("");
    setScheduledAt("");
    setProgress(0);
    setStage("pick");
  };

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 300 }}
      className="absolute inset-0 z-50 flex flex-col"
      style={{ background: t.bg }}
      role="dialog" aria-modal="true" aria-label="Create a post"
    >
      {/* ── Header ── */}
      <div className="flex items-center gap-3 px-5 pt-14 pb-4 flex-shrink-0"
        style={{ borderBottom: `1px solid ${t.divider}` }}>
        <button onClick={stage === "uploading" ? cancelUpload : onClose} aria-label="Close"
          className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 active:opacity-70"
          style={{ background: t.backBtnBg, border: t.cardBorder }}>
          <X className="w-4 h-4" style={{ color: t.heading }} />
        </button>
        <div className="min-w-0 flex-1">
          <h1 className="font-extrabold text-[22px] leading-tight" style={{ color: t.heading }}>
            {stage === "published" ? (published?.status === "scheduled" ? "Scheduled" : "Posted") : "New post"}
          </h1>
          <p className="text-[13px] mt-0.5" style={{ color: t.sub }}>
            {stage === "pick" && "Pick a video or photo to share"}
            {stage === "compose" && "Add a caption, then publish"}
            {stage === "uploading" && "Uploading — keep this screen open"}
            {stage === "published" && (published?.status === "scheduled" ? "Your post is scheduled" : "Your post is live")}
          </p>
        </div>
        {media && stage === "compose" && (
          <button onClick={clearMedia} aria-label="Remove media"
            className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ background: t.chipBg, border: t.chipBorder }}>
            <Trash2 className="w-4 h-4" style={{ color: t.sub }} />
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-5 pt-5 pb-8" style={{ scrollbarWidth: "none" }}>
        <input ref={fileRef} type="file" accept={ACCEPT_ATTRIBUTE} className="hidden"
          onChange={(e) => { void acceptFile(e.target.files?.[0]); e.target.value = ""; }} />

        {/* ── Error ── shown at every stage, above whatever caused it ── */}
        <AnimatePresence>
          {error && (
            <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className="rounded-2xl px-4 py-3 flex items-start gap-3 mb-4"
              style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)" }}
              role="alert">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: "#f87171" }} />
              <p className="text-[13px] leading-snug flex-1" style={{ color: "#f87171" }}>{error}</p>
              <button onClick={() => setError("")} aria-label="Dismiss">
                <X className="w-3.5 h-3.5" style={{ color: "#f87171" }} />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Pick ── */}
        {stage === "pick" && (
          <>
            <button
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              disabled={reading}
              className="w-full rounded-3xl flex flex-col items-center justify-center gap-3 py-14 px-6 text-center transition-colors"
              style={{
                background: dragging ? "rgba(0,174,239,0.12)" : t.groupBg,
                border: dragging ? `2px dashed ${ACCENT}` : `2px dashed ${isDark ? "rgba(0,174,239,0.25)" : "rgba(0,0,0,0.14)"}`,
              }}
            >
              <motion.div
                animate={reading ? { scale: [1, 1.08, 1] } : { scale: 1 }}
                transition={reading ? { duration: 1.1, repeat: Infinity } : { duration: 0.2 }}
                className="w-16 h-16 rounded-2xl flex items-center justify-center"
                style={{ background: "rgba(0,174,239,0.14)", border: `1px solid ${ACCENT}44`, color: ACCENT }}>
                <UploadIcon className="w-7 h-7" />
              </motion.div>
              <div>
                <p className="font-bold text-[16px]" style={{ color: t.heading }}>
                  {reading ? "Reading your file…" : "Choose a video or photo"}
                </p>
                <p className="text-[13px] mt-1 leading-relaxed" style={{ color: t.sub }}>
                  {reading ? "Checking it plays before we take it." : "Tap to browse, or drop a file here"}
                </p>
              </div>
            </button>

            <div className="grid grid-cols-2 gap-3 mt-4">
              {[
                { icon: <Film className="w-4 h-4" />, label: ACCEPTED_LABEL, sub: "Accepted formats" },
                { icon: <ImageIcon className="w-4 h-4" />, label: `Up to ${MAX_LABEL}`, sub: "Maximum size" },
              ].map((item) => (
                <div key={item.sub} className="rounded-2xl px-4 py-3"
                  style={{ background: t.groupBg, border: t.groupBorder }}>
                  <span className="flex items-center gap-2" style={{ color: ACCENT }}>{item.icon}
                    <span className="font-bold text-[13px]" style={{ color: t.heading }}>{item.label}</span>
                  </span>
                  <p className="text-[11px] mt-1" style={{ color: t.sub }}>{item.sub}</p>
                </div>
              ))}
            </div>

            <Callout icon={<Sparkles className="w-4 h-4" />} t={t}>
              Vertical clips under a minute get the most collab requests. Your file
              is checked in the browser before anything is uploaded.
            </Callout>
          </>
        )}

        {/* ── Compose & uploading ── the same draft, one of them read-only ── */}
        {(stage === "compose" || stage === "uploading") && media && (
          <>
            <MediaPreview media={media} busy={stage === "uploading"} />

            {stage === "uploading" ? (
              <UploadProgress fraction={progress} bytes={media.bytes} onCancel={cancelUpload} t={t} />
            ) : (
              <>
                <div className="mt-5">
                  <Field
                    label="Caption" t={t} value={caption} onChange={setCaption}
                    placeholder="Say what this is…" maxLength={300} multiline rows={3}
                    hint="Mention a creator with @handle — it becomes a link on the post."
                  />

                  <Field
                    label="Hashtags" t={t} value={tagInput} onChange={setTagInput}
                    placeholder="#producer #newmusic"
                    hint="Up to 10 tags. Separate them with spaces."
                  />
                  {hashtags.length > 0 && (
                    <div className="flex flex-wrap gap-2 -mt-2 mb-4 px-1">
                      {hashtags.map((tag) => (
                        <span key={tag} className="px-2.5 py-1 rounded-full text-[12px] font-semibold flex items-center gap-1"
                          style={{ background: "rgba(0,174,239,0.14)", border: `1px solid ${ACCENT}55`, color: ACCENT }}>
                          <Hash className="w-3 h-3" />{tag.slice(1)}
                        </span>
                      ))}
                    </div>
                  )}

                  <Field
                    label="Sound" t={t} value={audio} onChange={setAudio}
                    placeholder="Original Sound" maxLength={60}
                  />
                  <div className="flex flex-wrap gap-2 -mt-2 mb-5 px-1">
                    {SOUND_SUGGESTIONS.map((name) => (
                      <Chip key={name} label={name} selected={audio === name} onClick={() => setAudio(name)} t={t} />
                    ))}
                  </div>

                  <CollabPicker value={collabWith} onChange={setCollabWith} t={t} />

                  <p className="text-[11px] font-bold uppercase tracking-widest mb-2 px-1" style={{ color: t.sectionLbl }}>
                    Who can see this
                  </p>
                  <div className="rounded-2xl overflow-hidden mb-5" style={{ background: t.groupBg, border: t.groupBorder }}>
                    {VISIBILITY_OPTIONS.map((option, i) => (
                      <button key={option.value} onClick={() => setVisibility(option.value)}
                        className="w-full flex items-center gap-3 px-4 py-3.5 text-left"
                        style={{ borderBottom: i < VISIBILITY_OPTIONS.length - 1 ? `1px solid ${t.divider}` : "none" }}>
                        <span className="w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0"
                          style={{
                            background: visibility === option.value ? ACCENT : "transparent",
                            border: visibility === option.value ? "none" : `1.5px solid ${t.chevron}`,
                          }}>
                          {visibility === option.value && <Check className="w-3 h-3 text-white" strokeWidth={3.5} />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block font-semibold text-[14px]" style={{ color: t.heading }}>{option.label}</span>
                          <span className="block text-[12px] mt-0.5 leading-snug" style={{ color: t.sub }}>{option.hint}</span>
                        </span>
                      </button>
                    ))}
                  </div>

                  <div className="rounded-2xl overflow-hidden mb-6" style={{ background: t.groupBg, border: t.groupBorder }}>
                    <ToggleLine label="Allow comments" on={allowComments} onToggle={() => setAllowComments((v) => !v)} t={t} />
                    <ToggleLine label="Open to collab requests on this post" on={allowCollabs}
                      onToggle={() => setAllowCollabs((v) => !v)} t={t} last />
                  </div>

                  <div className="rounded-2xl px-4 py-3.5 mb-6" style={{ background: t.groupBg, border: t.groupBorder }}>
                    <label className="flex items-center gap-3 text-[14px] font-semibold" style={{ color: t.heading }}>
                      <Clock className="w-4 h-4" style={{ color: ACCENT }} />
                      <span className="min-w-0 flex-1">Schedule post</span>
                      <input
                        type="datetime-local"
                        min={new Date(Date.now() + 60_000).toISOString().slice(0, 16)}
                        value={scheduledAt}
                        onChange={(event) => setScheduledAt(event.target.value)}
                        className="min-w-0 max-w-[190px] rounded-lg px-2 py-1 text-[12px]"
                        style={{ background: t.chipBg, color: t.heading, border: t.chipBorder }}
                      />
                    </label>
                    <p className="text-[12px] mt-1.5" style={{ color: t.sub }}>
                      Leave empty to publish immediately.
                    </p>
                  </div>
                </div>

                <PrimaryAction onClick={publish}>
                  {scheduledAt ? <Clock className="w-4 h-4" /> : <UploadIcon className="w-4 h-4" />}
                  {scheduledAt ? "Schedule post" : "Publish post"}
                </PrimaryAction>
                <div className="h-3" />
                <SecondaryAction t={t} onClick={clearMedia}>Choose a different file</SecondaryAction>
              </>
            )}
          </>
        )}

        {/* ── Published ── */}
        {stage === "published" && published && (
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="pt-4">
            <div className="flex flex-col items-center text-center">
              <motion.div
                initial={{ scale: 0 }} animate={{ scale: [0, 1.2, 1] }}
                transition={{ duration: 0.45, ease: [0.17, 0.89, 0.32, 1.28] }}
                className="w-20 h-20 rounded-full flex items-center justify-center mb-4"
                style={{ background: `linear-gradient(135deg,${ACCENT},#0077cc)`, boxShadow: "0 10px 34px rgba(0,174,239,0.45)" }}>
                <Check className="w-9 h-9 text-white" strokeWidth={3} />
              </motion.div>
              <p className="font-extrabold text-[19px]" style={{ color: t.heading }}>
                {published.status === "scheduled" ? "Your post is scheduled" : "Your post is live"}
              </p>
              <p className="text-[13px] mt-1.5 leading-relaxed max-w-[280px]" style={{ color: t.sub }}>
                {published.status === "scheduled"
                  ? `It will publish on ${new Date(published.scheduledAt ?? "").toLocaleString()}.`
                  : published.visibility === "public"
                  ? "It's in the For You feed and on your profile. Views start landing in your dashboard right away."
                  : published.visibility === "followers"
                    ? "Your followers will see it in their Following feed."
                    : "Only you can see this one — change that any time from your profile."}
              </p>
            </div>

            <div className="rounded-2xl overflow-hidden mt-6 mb-6 flex items-center gap-3 p-3"
              style={{ background: t.groupBg, border: t.groupBorder }}>
              <div className="w-16 h-20 rounded-xl overflow-hidden flex-shrink-0" style={{ background: "rgba(0,0,0,0.25)" }}>
                {published.thumbnail
                  ? <Thumb src={published.thumbnail} className="w-full h-full object-cover" />
                  : <div className="w-full h-full" style={{ background: "linear-gradient(135deg, rgba(0,174,239,0.22), rgba(124,58,237,0.18))" }} />}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-semibold line-clamp-2" style={{ color: t.heading }}>
                  {published.caption || "No caption"}
                </p>
                <p className="text-[11px] mt-1 flex items-center gap-2" style={{ color: t.sub }}>
                  <Music className="w-3 h-3" />{published.audio}
                  {published.durationSec > 0 && <><Clock className="w-3 h-3" />{formatDuration(published.durationSec)}</>}
                </p>
              </div>
            </div>

            {onViewPost && published.visibility !== "private" && (
              <>
                <PrimaryAction onClick={() => onViewPost(published.id)}>View post in feed</PrimaryAction>
                <div className="h-3" />
              </>
            )}
            <SecondaryAction t={t} onClick={postAnother}>Post another</SecondaryAction>
            <div className="h-3" />
            <SecondaryAction t={t} onClick={onClose}>Done</SecondaryAction>
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

// ─── PREVIEW ─────────────────────────────────────────────────────────────────

/**
 * What is about to be posted, rendered the way the feed will render it: a
 * 9:16 frame, cover-cropped. A video loops muted so the crop can be judged
 * without the sound of the room it was shot in.
 */
function MediaPreview({ media, busy }: { media: PickedMedia; busy: boolean }) {
  const isDark = useTheme();
  const t = useTokens(isDark);

  return (
    <div className="rounded-3xl overflow-hidden" style={{ border: t.groupBorder, background: "rgba(0,0,0,0.35)" }}>
      <div className="relative w-full" style={{ aspectRatio: "9 / 14" }}>
        {media.kind === "video" ? (
          <video src={media.url} className="absolute inset-0 w-full h-full object-cover"
            autoPlay loop muted playsInline />
        ) : (
          <img src={media.url} alt="Selected media" className="absolute inset-0 w-full h-full object-cover" />
        )}
        <div className="absolute inset-x-0 bottom-0 h-24"
          style={{ background: "linear-gradient(to top, rgba(0,0,0,0.8), transparent)" }} />
        <div className="absolute bottom-3 left-3 right-3 flex items-center gap-2 flex-wrap">
          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold text-white"
            style={{ background: "rgba(0,174,239,0.9)" }}>
            {media.kind === "video" ? "VIDEO" : "PHOTO"}
          </span>
          {media.durationSec > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold text-white"
              style={{ background: "rgba(0,0,0,0.55)" }}>{formatDuration(media.durationSec)}</span>
          )}
          {media.width > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold text-white"
              style={{ background: "rgba(0,0,0,0.55)" }}>{media.width}×{media.height}</span>
          )}
          <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold text-white"
            style={{ background: "rgba(0,0,0,0.55)" }}>{formatBytes(media.bytes)}</span>
        </div>
        {busy && <div className="absolute inset-0" style={{ background: "rgba(0,0,0,0.35)" }} />}
      </div>
      <p className="text-[11px] px-4 py-2.5 truncate" style={{ color: t.sub }}>{media.name}</p>
    </div>
  );
}

// ─── PROGRESS ────────────────────────────────────────────────────────────────

/**
 * Bytes, not a spinner: the wait is proportional to the file, so the bar reports
 * how much of it has landed and the label says it in megabytes. Cancel stays
 * available the whole way — an upload the user cannot stop is a trap.
 */
function UploadProgress({
  fraction, bytes, onCancel, t,
}: {
  fraction: number; bytes: number; onCancel: () => void; t: ReturnType<typeof useTokens>;
}) {
  const pct = Math.round(fraction * 100);
  return (
    <div className="mt-6">
      <div className="flex items-baseline justify-between mb-2 px-1">
        <p className="font-bold text-[15px]" style={{ color: t.heading }}>
          {pct < 100 ? "Uploading…" : "Finishing up…"}
        </p>
        <p className="text-[13px] font-semibold" style={{ color: ACCENT }}>{pct}%</p>
      </div>
      <div className="h-2.5 rounded-full overflow-hidden" style={{ background: t.chipBg }}
        role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <motion.div className="h-full rounded-full"
          animate={{ width: `${pct}%` }} transition={{ ease: "linear", duration: 0.2 }}
          style={{ background: `linear-gradient(90deg,${ACCENT},#7c3aed)` }} />
      </div>
      <p className="text-[12px] mt-2 px-1" style={{ color: t.sub }}>
        {formatBytes(Math.round(bytes * fraction))} of {formatBytes(bytes)}
      </p>
      <div className="h-5" />
      <SecondaryAction t={t} onClick={onCancel}>Cancel upload</SecondaryAction>
    </div>
  );
}

// ─── COLLAB PICKER ───────────────────────────────────────────────────────────

/** Tagging a co-creator is what puts the post in the Collabs tab on a profile. */
function CollabPicker({
  value, onChange, t,
}: {
  value: string; onChange: (handle: string) => void; t: ReturnType<typeof useTokens>;
}) {
  const following = useFollowingCreators();
  // Someone you follow first, then the rest of the directory, so the common
  // case is one tap rather than a search.
  const options = (following.length ? following : CREATORS.slice(0, 6)).slice(0, 6);

  return (
    <div className="mb-5">
      <p className="text-[11px] font-bold uppercase tracking-widest mb-2 px-1" style={{ color: t.sectionLbl }}>
        Collab partner <span style={{ color: t.sub }}>· optional</span>
      </p>
      <div className="flex gap-2 overflow-x-auto pb-1" style={{ scrollbarWidth: "none" }}>
        <button onClick={() => onChange("")}
          className="px-3.5 py-2 rounded-full text-[13px] font-semibold flex-shrink-0 flex items-center gap-1.5"
          style={{
            background: value === "" ? "rgba(0,174,239,0.2)" : t.chipBg,
            border: value === "" ? `1px solid ${ACCENT}` : t.chipBorder,
            color: value === "" ? ACCENT : t.sub,
          }}>
          <Users className="w-3.5 h-3.5" /> Solo
        </button>
        {options.map((creator) => {
          const on = value === creator.username;
          return (
            <button key={creator.id} onClick={() => onChange(on ? "" : creator.username)}
              className="pl-1.5 pr-3 py-1.5 rounded-full text-[13px] font-semibold flex-shrink-0 flex items-center gap-2"
              style={{
                background: on ? "rgba(0,174,239,0.2)" : t.chipBg,
                border: on ? `1px solid ${ACCENT}` : t.chipBorder,
                color: on ? ACCENT : t.sub,
              }}>
              <Avatar src={creator.avatarUrl} name={creator.displayName} color={creator.avatarColor} size={22} />
              @{creator.username}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── TOGGLE LINE ─────────────────────────────────────────────────────────────

function ToggleLine({
  label, on, onToggle, t, last = false,
}: {
  label: string; on: boolean; onToggle: () => void; t: ReturnType<typeof useTokens>; last?: boolean;
}) {
  return (
    <button onClick={onToggle} role="switch" aria-checked={on}
      className="w-full flex items-center justify-between gap-3 px-4 py-3.5 text-left"
      style={{ borderBottom: last ? "none" : `1px solid ${t.divider}` }}>
      <span className="text-[14px] font-semibold" style={{ color: t.heading }}>{label}</span>
      <span className="w-11 h-6 rounded-full flex items-center px-0.5 flex-shrink-0 transition-colors"
        style={{ background: on ? ACCENT : t.switchOff, justifyContent: on ? "flex-end" : "flex-start" }}>
        <motion.span layout transition={{ type: "spring", stiffness: 500, damping: 32 }}
          className="w-5 h-5 rounded-full bg-white" />
      </span>
    </button>
  );
}
