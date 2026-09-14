import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { TrendingSounds } from "./TrendingSounds";
import { AuthFlow } from "./Auth";
import { SettingsScreen, DeleteProfileModal } from "./Settings";
import { UploadScreen } from "./Upload";
import { type Account, getSession, endSession } from "./auth-store";
import { GoLiveSetup, CreatorLiveView, ViewerLiveView, LiveBannerStrip } from "./LiveStream";
import { InboxScreen } from "./Inbox";
import { ThemeContext, useTheme } from "./ThemeContext";
import { creatorById, type FeedVideo } from "./creators";
import { useFeed } from "./feed-store";
import { addComment, deleteComment, editComment, fetchComments, likeComment, reportComment, type GraphQLComment, trackPostWatch, unlikeComment } from "./profile-graphql";
import { activateFollowGraph, useFollow } from "./follow-store";
import { activateLikeGraph, useLike } from "./like-store";
import { activateSaveGraph, useSave } from "./save-store";
import { activateShareGraph, useShare } from "./share-store";
import { activatePosts } from "./posts-store";
import { motion, AnimatePresence } from "motion/react";
import {
  Heart, MessageCircle, Bookmark, Music,
  Home, Search, Plus, Mail, User, X, Send, Check,
  ChevronUp, ChevronDown, Navigation, Trash2,
} from "lucide-react";

// ─── FEED ADAPTER ────────────────────────────────────────────────────────────
//
// The screen renders one shape for a slide, `DisplayVideo` — a post's own
// counters plus whatever the creator directory knows about who posted it.
// `FeedVideo` only carries a `creatorId`, so the lookup happens once here
// rather than at every place a video is rendered.

interface DisplayVideo {
  id: string;
  creatorId: string;
  username: string;
  avatarUrl: string;
  collabStatus: string;
  collabScore: number;
  collabCount: number;
  caption: string;
  hashtags: string[];
  audio: string;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  thumbnail: string;
  /** An uploaded video file, when the post has one — image-only posts have none. */
  mediaUrl?: string;
  isLiked: boolean;
  isSaved: boolean;
}

function toDisplayVideo(item: FeedVideo): DisplayVideo {
  const creator = creatorById(item.creatorId);
  return {
    id: item.id,
    creatorId: item.creatorId,
    username: creator?.username ?? "",
    avatarUrl: creator?.avatarUrl ?? "",
    collabStatus: creator?.collabStatus ?? "",
    collabScore: creator?.collabScore ?? 0,
    collabCount: creator?.collabCount ?? 0,
    caption: item.caption,
    hashtags: item.hashtags,
    audio: item.audio,
    likes: item.likes,
    comments: item.comments,
    shares: item.shares,
    saves: item.saves,
    thumbnail: item.thumbnail,
    mediaUrl: item.mediaUrl,
    isLiked: item.isLiked ?? false,
    isSaved: item.isSaved ?? false,
  };
}

/** The video background for a slide whose post has an uploaded video file. */
function PostVideo({ mediaUrl, isActive, paused }: { mediaUrl: string; isActive: boolean; paused: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (isActive && !paused) el.play().catch(() => {});
    else el.pause();
  }, [isActive, paused]);

  return (
    <video
      ref={ref}
      src={mediaUrl}
      loop
      muted
      playsInline
      className="absolute inset-0 w-full h-full object-cover"
    />
  );
}

// ─── COMMENTS DATA ───────────────────────────────────────────────────────────

interface Comment {
  id: string;
  username: string;
  avatarUrl: string;
  text: string;
  likes: number;
  time: string;
  isLiked?: boolean;
  canDelete?: boolean;
  canEdit?: boolean;
}

const fmt = (n: number) =>
  n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M"
  : n >= 1_000 ? (n / 1_000).toFixed(1) + "K"
  : String(n);

// ─── COLLAB EXPLOSION SHARDS ──────────────────────────────────────────────────

const SHARDS = Array.from({ length: 24 }, (_, i) => {
  const angle = (i / 24) * Math.PI * 2 + (i % 4) * 0.22;
  const dist = 58 + (i % 5) * 20;
  return {
    id: i,
    tx: Math.cos(angle) * dist,
    ty: Math.sin(angle) * dist,
    rotate: (i * 83) % 360,
    w: 5 + (i % 5) * 3,
    h: 3 + (i % 4) * 3,
    delay: i * 0.012,
    color: ["#00AEEF", "#38bdf8", "#7dd3fc", "#0ea5e9", "#ffffff"][i % 5],
  };
});

// ─── COLLAB BUTTON ────────────────────────────────────────────────────────────

type CPhase = "idle" | "exploding" | "gone" | "reappearing";

function CollabButton({ onTap }: { onTap: () => void }) {
  const [phase, setPhase] = useState<CPhase>("idle");

  const fire = useCallback(() => {
    if (phase !== "idle") return;
    setPhase("exploding");
    setTimeout(() => onTap(), 160);
    setTimeout(() => setPhase("gone"), 420);
    setTimeout(() => setPhase("reappearing"), 980);
    setTimeout(() => setPhase("idle"), 1500);
  }, [phase, onTap]);

  const exploding = phase === "exploding";
  const gone = phase === "gone";
  const reappearing = phase === "reappearing";
  const showMain = phase === "idle" || exploding;

  return (
    <button onClick={fire} className="flex flex-col items-center gap-1.5" aria-label="Collab">
      <div className="relative" style={{ width: 52, height: 52 }}>
        {SHARDS.map((s) => (
          <motion.div
            key={s.id}
            initial={{ x: 0, y: 0, rotate: 0, opacity: 0, scale: 0 }}
            animate={
              exploding
                ? { x: s.tx, y: s.ty, rotate: s.rotate, opacity: [0, 1, 1, 0], scale: [0.2, 1.3, 0.6, 0] }
                : { x: 0, y: 0, rotate: 0, opacity: 0, scale: 0 }
            }
            transition={{ duration: 0.65, delay: s.delay, ease: [0.1, 0.9, 0.2, 1] }}
            style={{
              position: "absolute", top: "50%", left: "50%",
              width: s.w, height: s.h,
              marginTop: -s.h / 2, marginLeft: -s.w / 2,
              borderRadius: 2, backgroundColor: s.color,
              boxShadow: `0 0 10px 3px ${s.color}80`,
            }}
          />
        ))}

        <AnimatePresence>
          {exploding && (
            <motion.div
              key="wave"
              initial={{ scale: 0.3, opacity: 1 }}
              animate={{ scale: 4.5, opacity: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.55, ease: "easeOut" }}
              style={{
                position: "absolute", inset: 0, borderRadius: "50%",
                border: "2px solid #00AEEF",
                boxShadow: "0 0 20px 6px rgba(0,174,239,0.5)",
              }}
            />
          )}
        </AnimatePresence>

        <AnimatePresence mode="wait">
          {showMain && (
            <motion.div
              key="circle"
              initial={false}
              animate={
                exploding
                  ? { scale: [1, 1.45, 0], opacity: [1, 1, 0], filter: ["blur(0px)", "blur(0px)", "blur(10px)"] }
                  : { scale: [1, 1.07, 1], opacity: 1, filter: "blur(0px)" }
              }
              transition={
                exploding
                  ? { duration: 0.32, ease: "easeIn" }
                  : { duration: 2.6, repeat: Infinity, ease: "easeInOut" }
              }
              className="absolute inset-0 rounded-full flex items-center justify-center"
              style={{
                background: "linear-gradient(135deg, #00AEEF 0%, #0077cc 100%)",
                boxShadow: "0 0 0 2.5px rgba(0,174,239,0.3), 0 0 22px rgba(0,174,239,0.45), 0 4px 18px rgba(0,0,0,0.6)",
              }}
            >
              <span className="text-white font-bold text-xl leading-none select-none">C</span>
            </motion.div>
          )}
          {reappearing && (
            <motion.div
              key="reappear"
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: [0, 1.35, 1], opacity: [0, 1, 1] }}
              transition={{ duration: 0.52, ease: [0.17, 0.89, 0.32, 1.35] }}
              className="absolute inset-0 rounded-full flex items-center justify-center"
              style={{
                background: "linear-gradient(135deg, #00AEEF 0%, #0077cc 100%)",
                boxShadow: "0 0 0 2.5px rgba(0,174,239,0.3), 0 0 22px rgba(0,174,239,0.45), 0 4px 18px rgba(0,0,0,0.6)",
              }}
            >
              <span className="text-white font-bold text-xl leading-none select-none">C</span>
            </motion.div>
          )}
        </AnimatePresence>
        {gone && <div className="absolute inset-0" />}
      </div>
      <span className="text-white/90 text-[10px] font-semibold tracking-wide uppercase">Collab</span>
    </button>
  );
}

// ─── COMMENT SHEET ───────────────────────────────────────────────────────────

const commentTime = (createdAt: string) => {
  const minutes = Math.max(1, Math.floor((Date.now() - new Date(createdAt).getTime()) / 60_000));
  return minutes < 60 ? `${minutes}m` : minutes < 1_440 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / 1_440)}d`;
};

const toComment = (comment: GraphQLComment): Comment => ({
  id: comment.id,
  username: comment.author.username,
  avatarUrl: comment.author.avatarUrl ?? "",
  text: comment.text,
  likes: comment.likes,
  time: commentTime(comment.createdAt),
  isLiked: comment.isLiked,
  canDelete: comment.canDelete,
  canEdit: comment.canEdit,
});

function CommentSheet({
  video, onClose, onCommentCountChange,
}: {
  video: DisplayVideo; onClose: () => void; onCommentCountChange: (delta: number) => void;
}) {
  const isDark = useTheme();
  const [text, setText] = useState("");
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void fetchComments(video.id).then((result) => {
      if (!active) return;
      if (!result.ok) { setError(result.error); setLoading(false); return; }
      setComments(result.value.map(toComment));
      setLoading(false);
    });
    return () => { active = false; };
  }, [video.id]);

  const submit = async () => {
    const t = text.trim();
    if (!t || submitting) return;
    setSubmitting(true);
    const result = await addComment(video.id, t);
    if (!result.ok) { setError(result.error); setSubmitting(false); return; }
    setComments((previous) => [...previous, toComment(result.value)]);
    onCommentCountChange(1);
    setText("");
    setSubmitting(false);
    setTimeout(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); }, 50);
  };

  const remove = async (id: string) => {
    const result = await deleteComment(id);
    if (!result.ok) { setError(result.error); return; }
    setComments((previous) => previous.filter((comment) => comment.id !== id));
    onCommentCountChange(-1);
  };

  const edit = async (comment: Comment) => {
    const nextText = window.prompt("Edit comment", comment.text)?.trim();
    if (!nextText || nextText === comment.text) return;
    const result = await editComment(comment.id, nextText);
    if (!result.ok) { setError(result.error); return; }
    setComments((previous) => previous.map((item) => item.id === comment.id ? toComment(result.value) : item));
  };

  const report = async (id: string) => {
    const reason = window.prompt("Why are you reporting this comment?", "Inappropriate content")?.trim();
    if (!reason) return;
    const result = await reportComment(id, reason);
    if (!result.ok) setError(result.error);
    else setComments((previous) => previous.filter((comment) => comment.id !== id));
  };

  const toggleLike = async (comment: Comment) => {
    const result = comment.isLiked ? await unlikeComment(comment.id) : await likeComment(comment.id);
    if (!result.ok) { setError(result.error); return; }
    setComments((previous) => previous.map((item) => item.id === comment.id
      ? { ...item, likes: result.value.likes, isLiked: result.value.liked }
      : item));
  };

  const D = {
    sheetBg: isDark ? "linear-gradient(180deg,#1c1c24 0%,#14141a 100%)" : "linear-gradient(180deg,#ffffff 0%,#f7f9ff 100%)",
    border: isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)",
    shadow: isDark ? "0 -20px 60px rgba(0,0,0,0.7)" : "0 -20px 60px rgba(0,0,0,0.12)",
    handle: isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.12)",
    divider: isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.07)",
    heading: isDark ? "#fff" : "#0a0e1a",
    xBg: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
    xIcon: isDark ? "rgba(255,255,255,0.7)" : "rgba(10,14,26,0.5)",
    inputColor: isDark ? "#fff" : "#0a0e1a",
  };

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 320 }}
      className="absolute inset-x-0 bottom-0 z-50 rounded-t-3xl flex flex-col"
      style={{ height: "72%", background: D.sheetBg, border: D.border, borderBottom: "none", boxShadow: D.shadow }}
    >
      <div className="flex justify-center pt-3 pb-2 flex-shrink-0">
        <div className="w-9 h-1 rounded-full" style={{ background: D.handle }} />
      </div>
      <div className="flex items-center justify-between px-5 pb-3 flex-shrink-0" style={{ borderBottom: `1px solid ${D.divider}` }}>
        <span className="font-bold text-[15px]" style={{ color: D.heading }}>
          {fmt(comments.length)} comments
        </span>
        <button onClick={onClose} className="w-7 h-7 rounded-full flex items-center justify-center" style={{ background: D.xBg }}>
          <X className="w-3.5 h-3.5" style={{ color: D.xIcon }} />
        </button>
      </div>
      <div ref={listRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-5">
        {loading ? <p className="text-center text-sm" style={{ color: D.xIcon }}>Loading comments…</p>
          : comments.length === 0 ? <p className="text-center text-sm" style={{ color: D.xIcon }}>Be the first to comment.</p>
          : comments.map((c) => <CommentRow key={c.id} comment={c} onDelete={remove} onEdit={edit} onReport={report} onLike={toggleLike} />)}
      </div>
      {error && <p className="px-4 pb-2 text-[12px]" style={{ color: "#f87171" }} role="alert">{error}</p>}
      <div className="flex-shrink-0 flex items-center gap-3 px-4 py-3" style={{ borderTop: `1px solid ${D.divider}` }}>
        <div className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs font-bold" style={{ background: "linear-gradient(135deg,#00AEEF,#0077cc)" }}>Y</div>
        <input
          ref={inputRef} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }}
          placeholder="Add a comment…" autoFocus
          className="flex-1 bg-transparent text-sm outline-none"
          style={{ color: D.inputColor }}
        />
        <motion.button whileTap={{ scale: 0.88 }} onClick={() => void submit()} disabled={submitting} style={{ opacity: text.trim() && !submitting ? 1 : 0.3 }}>
          <Send className="w-5 h-5" style={{ color: "#00AEEF" }} />
        </motion.button>
      </div>
    </motion.div>
  );
}

function CommentRow({ comment, onDelete, onEdit, onReport, onLike }: {
  comment: Comment;
  onDelete: (id: string) => void;
  onEdit: (comment: Comment) => void;
  onReport: (id: string) => void;
  onLike: (comment: Comment) => void;
}) {
  const isDark = useTheme();
  const text1 = isDark ? "#fff" : "#0a0e1a";
  const text2 = isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.4)";
  const text3 = isDark ? "rgba(255,255,255,0.85)" : "rgba(10,14,26,0.75)";
  const text4 = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.35)";
  return (
    <div className="flex gap-3">
      <img src={comment.avatarUrl} alt={comment.username} className="w-8 h-8 rounded-full object-cover flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-[13px] font-semibold" style={{ color: text1 }}>@{comment.username}</span>
          <span className="text-[11px]" style={{ color: text2 }}>{comment.time}</span>
        </div>
        <p className="text-[13px] leading-snug mt-0.5" style={{ color: text3 }}>{comment.text}</p>
        <div className="flex items-center gap-3 mt-1">
          <button className="text-[11px] font-medium" style={{ color: text2 }}>Reply</button>
          {comment.canEdit && <button onClick={() => void onEdit(comment)} className="text-[11px] font-medium" style={{ color: text2 }}>Edit</button>}
          <button onClick={() => void onReport(comment.id)} className="text-[11px] font-medium" style={{ color: text2 }}>Report</button>
          {comment.canDelete && (
            <button onClick={() => void onDelete(comment.id)} aria-label="Delete comment" title="Delete comment">
              <Trash2 className="w-3.5 h-3.5" style={{ color: text2 }} />
            </button>
          )}
        </div>
      </div>
      <button onClick={() => void onLike(comment)} className="flex flex-col items-center gap-0.5 flex-shrink-0 pt-0.5" aria-label={comment.isLiked ? "Unlike comment" : "Like comment"}>
        <Heart className={`w-4 h-4 ${comment.isLiked ? "fill-red-500 text-red-500" : ""}`} style={{ color: comment.isLiked ? undefined : text4 }} />
        <span className="text-[10px]" style={{ color: text4 }}>{fmt(comment.likes)}</span>
      </button>
    </div>
  );
}

// ─── SHARE SHEET ─────────────────────────────────────────────────────────────

const PLATFORMS = [
  { id: "instagram", label: "Instagram", color: "#E1306C", bg: "linear-gradient(135deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888)", icon: "IG" },
  { id: "twitter",   label: "X / Twitter", color: "#fff",  bg: "#000",    icon: "𝕏" },
  { id: "whatsapp",  label: "WhatsApp",  color: "#fff",    bg: "#25D366", icon: "W" },
  { id: "tiktok",    label: "TikTok",    color: "#fff",    bg: "#010101", icon: "TK" },
  { id: "snapchat",  label: "Snapchat",  color: "#000",    bg: "#FFFC00", icon: "👻" },
  { id: "facebook",  label: "Facebook",  color: "#fff",    bg: "#1877F2", icon: "f" },
  { id: "telegram",  label: "Telegram",  color: "#fff",    bg: "#229ED9", icon: "✈" },
  { id: "reddit",    label: "Reddit",    color: "#fff",    bg: "#FF4500", icon: "r/" },
  { id: "messages",  label: "Messages",  color: "#fff",    bg: "#34C759", icon: "✉" },
];

function ShareSheet({ video, onClose, onShared }: { video: DisplayVideo; onClose: () => void; onShared: () => void }) {
  const isDark = useTheme();
  const [copied, setCopied] = useState(false);
  const [sharedTo, setSharedTo] = useState<string | null>(null);
  const fakeUrl = `https://connexionz.app/v/${video.id}`;

  const D = {
    sheetBg: isDark ? "linear-gradient(180deg,#1c1c24 0%,#14141a 100%)" : "linear-gradient(180deg,#ffffff 0%,#f7f9ff 100%)",
    border: isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)",
    shadow: isDark ? "0 -20px 60px rgba(0,0,0,0.7)" : "0 -20px 60px rgba(0,0,0,0.12)",
    handle: isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.12)",
    divider: isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.07)",
    heading: isDark ? "#fff" : "#0a0e1a",
    sub: isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.45)",
    xBg: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
    xIcon: isDark ? "rgba(255,255,255,0.7)" : "rgba(10,14,26,0.5)",
    labelColor: isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.5)",
    copyBarBg: isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)",
    copyBorder: isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)",
    urlColor: isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)",
  };

  const shareUrl = encodeURIComponent(fakeUrl);
  const shareText = encodeURIComponent(`${video.caption} @${video.username}`);

  const openShareTarget = async (platform: string): Promise<boolean> => {
    const targetUrls: Record<string, string> = {
      twitter: `https://twitter.com/intent/tweet?url=${shareUrl}&text=${shareText}`,
      whatsapp: `https://wa.me/?text=${shareText}%20${shareUrl}`,
      facebook: `https://www.facebook.com/sharer/sharer.php?u=${shareUrl}`,
      telegram: `https://t.me/share/url?url=${shareUrl}&text=${shareText}`,
      reddit: `https://www.reddit.com/submit?url=${shareUrl}&title=${shareText}`,
    };

    if (targetUrls[platform]) {
      window.open(targetUrls[platform], "_blank", "noopener,noreferrer,width=700,height=700");
      return true;
    }

    if (navigator.share) {
      try {
        await navigator.share({ title: `@${video.username} on ConnextionZ`, text: video.caption, url: fakeUrl });
        return true;
      } catch {
        return false;
      }
    }

    await navigator.clipboard.writeText(fakeUrl).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    return true;
  };

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 320 }}
      className="absolute inset-x-0 bottom-0 z-50 rounded-t-3xl"
      style={{ background: D.sheetBg, border: D.border, borderBottom: "none", boxShadow: D.shadow }}
    >
      <div className="flex justify-center pt-3 pb-2">
        <div className="w-9 h-1 rounded-full" style={{ background: D.handle }} />
      </div>
      <div className="flex items-center justify-between px-5 pb-4" style={{ borderBottom: `1px solid ${D.divider}` }}>
        <div>
          <p className="font-bold text-[15px]" style={{ color: D.heading }}>Share</p>
          <p className="text-[12px] mt-0.5" style={{ color: D.sub }}>@{video.username}'s video</p>
        </div>
        <button onClick={onClose} className="w-7 h-7 rounded-full flex items-center justify-center" style={{ background: D.xBg }}>
          <X className="w-3.5 h-3.5" style={{ color: D.xIcon }} />
        </button>
      </div>
      <div className="grid grid-cols-4 gap-x-3 gap-y-5 px-5 py-5">
        {PLATFORMS.map((p) => (
          <motion.button key={p.id} whileTap={{ scale: 0.88 }}
            onClick={() => {
              void openShareTarget(p.id).then((didShare) => {
                if (!didShare) return;
                onShared();
                setSharedTo(p.id);
                setTimeout(() => setSharedTo(null), 1500);
              });
            }}
            className="flex flex-col items-center gap-2"
          >
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-lg font-bold relative overflow-hidden"
              style={{ background: p.bg, color: p.color, boxShadow: "0 4px 14px rgba(0,0,0,0.25)" }}>
              {sharedTo === p.id
                ? <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} className="absolute inset-0 flex items-center justify-center rounded-2xl" style={{ background: "rgba(0,0,0,0.45)" }}>
                    <Check className="w-6 h-6 text-white" />
                  </motion.div>
                : <span className="select-none">{p.icon}</span>}
            </div>
            <span className="text-[10px] font-medium" style={{ color: D.labelColor }}>{p.label}</span>
          </motion.button>
        ))}
      </div>
      <div className="px-5 pb-8">
        <div className="flex items-center gap-3 rounded-2xl px-4 py-3" style={{ background: D.copyBarBg, border: D.copyBorder }}>
          <span className="flex-1 text-[12px] truncate" style={{ color: D.urlColor }}>{fakeUrl}</span>
          <motion.button whileTap={{ scale: 0.92 }} onClick={() => { navigator.clipboard.writeText(fakeUrl).catch(() => {}); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
            className="px-4 py-1.5 rounded-xl text-[12px] font-bold flex-shrink-0"
            style={{ background: copied ? "rgba(0,174,239,0.2)" : "rgba(0,174,239,0.15)", border: "1px solid rgba(0,174,239,0.4)", color: "#00AEEF" }}>
            {copied ? "Copied!" : "Copy Link"}
          </motion.button>
        </div>
      </div>
    </motion.div>
  );
}

// ─── ACTION RAIL ──────────────────────────────────────────────────────────────

function ActionRail({
  video, liked, likeCount, saved, saveCount, commentCount, shareCount, onLike, onSave, onCollab, onComment, onShare,
}: {
  video: DisplayVideo; liked: boolean; likeCount: number; saved: boolean; saveCount: number; commentCount: number; shareCount: number;
  onLike: () => void; onSave: () => void; onCollab: () => void; onComment: () => void; onShare: () => void;
}) {
  const followState = useFollow(video.creatorId);

  return (
    <div
      className="flex flex-col items-center gap-5 lg:gap-6 absolute right-6 lg:right-8 top-1/2 -translate-y-1/2 z-10"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="relative mb-1">
        <button
          type="button"
          onClick={() => { void followState.toggle(); }}
          disabled={followState.pending}
          aria-label={followState.following ? `Unfollow @${video.username}` : `Follow @${video.username}`}
          className="block rounded-full"
        >
          <img src={video.avatarUrl} alt={video.username} className="w-11 h-11 rounded-full object-cover border-2 border-white" />
        </button>
        <button
          type="button"
          onClick={() => { void followState.toggle(); }}
          disabled={followState.pending}
          aria-label={followState.following ? `Unfollow @${video.username}` : `Follow @${video.username}`}
          className="absolute -bottom-2 left-1/2 -translate-x-1/2 w-5 h-5 rounded-full flex items-center justify-center text-white text-xs font-bold"
          style={{ background: followState.following ? "#18b981" : "#00AEEF", boxShadow: "0 2px 8px rgba(0,174,239,0.5)" }}
        >
          {followState.following ? "✓" : "+"}
        </button>
      </div>
      <motion.button whileTap={{ scale: 0.85 }} onClick={onLike} className="flex flex-col items-center gap-1">
        <motion.div animate={liked ? { scale: [1, 1.35, 1] } : {}} transition={{ duration: 0.25 }}>
          <Heart className={`w-7 h-7 drop-shadow-lg ${liked ? "fill-red-500 text-red-500" : "text-white"}`} />
        </motion.div>
        <span className="text-white text-[11px] font-semibold">{fmt(likeCount)}</span>
      </motion.button>
      <div className="flex flex-col items-center gap-1">
        <motion.button whileTap={{ scale: 0.85 }} onClick={onComment}>
          <MessageCircle className="w-7 h-7 text-white drop-shadow-lg" />
        </motion.button>
        <span className="text-white text-[11px] font-semibold">{fmt(commentCount)}</span>
      </div>
      <CollabButton onTap={onCollab} />
      <motion.button whileTap={{ scale: 0.85 }} onClick={onSave} className="flex flex-col items-center gap-1">
        <motion.div animate={saved ? { scale: [1, 1.35, 1] } : {}} transition={{ duration: 0.25 }}>
          <Bookmark className={`w-7 h-7 drop-shadow-lg ${saved ? "fill-yellow-400 text-yellow-400" : "text-white"}`} />
        </motion.div>
        <span className="text-white text-[11px] font-semibold">{fmt(saveCount)}</span>
      </motion.button>
      <motion.button whileTap={{ scale: 0.85 }} onClick={onShare} className="flex flex-col items-center gap-1">
        <Navigation className="w-7 h-7 text-white drop-shadow-lg" />
        <span className="text-white text-[11px] font-semibold">{fmt(shareCount)}</span>
      </motion.button>
    </div>
  );
}

// ─── VIDEO INFO (BOTTOM LEFT) ─────────────────────────────────────────────────

function VideoInfo({ video }: { video: DisplayVideo }) {
  return (
    <div className="absolute left-4 bottom-28 right-20 z-10 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-white font-bold text-[15px]">@{video.username}</span>
        {video.collabStatus === "Available for Collaboration" && (
          <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
            style={{ background: "rgba(0,174,239,0.18)", border: "1px solid rgba(0,174,239,0.5)", color: "#00AEEF" }}>
            ✦ Open to Collab
          </span>
        )}
      </div>
      <p className="text-white/90 text-[13px] leading-snug line-clamp-2">{video.caption}</p>
      <p className="text-[13px] font-medium" style={{ color: "#00AEEF" }}>{video.hashtags.join(" ")}</p>
      <div className="flex items-center gap-2 mt-1">
        <div className="w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ background: "rgba(255,255,255,0.12)", backdropFilter: "blur(8px)" }}>
          <Music className="w-2.5 h-2.5 text-white" />
        </div>
        <motion.span animate={{ x: [0, -60, 0] }} transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
          className="text-white/75 text-[12px] whitespace-nowrap" style={{ display: "inline-block" }}>
          {video.audio}
        </motion.span>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full"
          style={{ background: "rgba(0,174,239,0.12)", border: "1px solid rgba(0,174,239,0.3)", backdropFilter: "blur(8px)" }}>
          <span style={{ color: "#00AEEF", fontSize: 11 }}>⭐</span>
          <span className="font-bold text-[12px]" style={{ color: "#00AEEF" }}>{video.collabScore.toFixed(1)}</span>
          <span className="text-white/40 text-[11px]">Collab Score</span>
          <span className="text-white/25 text-[11px]">·</span>
          <span className="text-white/40 text-[11px]">{video.collabCount} collabs</span>
        </div>
      </div>
    </div>
  );
}

// ─── COLLAB SHEET ─────────────────────────────────────────────────────────────

const COLLAB_TYPES = [
  { icon: "🎵", label: "Music" }, { icon: "📹", label: "Video" },
  { icon: "🎙", label: "Podcast" }, { icon: "📈", label: "Business" },
  { icon: "📸", label: "Photo" }, { icon: "🎮", label: "Gaming" },
  { icon: "💼", label: "Brand Deal" }, { icon: "✨", label: "Custom" },
];
const BUDGETS = ["Under $500", "$500–$2K", "$2K–$10K", "$10K+", "Open"];
const TIMELINES = ["ASAP", "1–2 weeks", "1 month", "3+ months"];

function CollabSheet({ video, onClose }: { video: DisplayVideo; onClose: () => void }) {
  const isDark = useTheme();
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [budget, setBudget] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const D = {
    sheetBg: isDark ? "linear-gradient(180deg,#1a1a22 0%,#14141a 100%)" : "linear-gradient(180deg,#ffffff 0%,#f7f9ff 100%)",
    border: isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)",
    handle: isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.12)",
    heading: isDark ? "#fff" : "#0a0e1a",
    xBg: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
    xIcon: isDark ? "rgba(255,255,255,0.7)" : "rgba(10,14,26,0.5)",
    label: isDark ? "rgba(255,255,255,0.5)" : "rgba(10,14,26,0.4)",
    tileUnselBg: isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)",
    tileUnselBorder: isDark ? "1px solid rgba(255,255,255,0.06)" : "1px solid rgba(0,0,0,0.07)",
    tileText: isDark ? "rgba(255,255,255,0.8)" : "rgba(10,14,26,0.7)",
    areaBg: isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)",
    areaBorder: isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)",
    areaColor: isDark ? "rgba(255,255,255,0.9)" : "#0a0e1a",
    chipUnsel: isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)",
    chipBorder: isDark ? "1px solid rgba(255,255,255,0.06)" : "1px solid rgba(0,0,0,0.08)",
    chipColor: isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.55)",
    btnDisBg: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.07)",
    btnDisColor: isDark ? "rgba(255,255,255,0.3)" : "rgba(10,14,26,0.3)",
  };

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 32, stiffness: 320 }}
      className="absolute inset-x-0 bottom-0 z-50 rounded-t-3xl overflow-hidden"
      style={{ background: D.sheetBg, border: D.border, borderBottom: "none", maxHeight: "88%", boxShadow: isDark ? "0 -20px 60px rgba(0,0,0,0.7)" : "0 -20px 60px rgba(0,0,0,0.12)" }}
    >
      <div className="flex justify-center pt-3 pb-1">
        <div className="w-9 h-1 rounded-full" style={{ background: D.handle }} />
      </div>
      <div className="overflow-y-auto px-5 pb-8" style={{ maxHeight: "calc(88vh - 20px)" }}>
        <div className="flex items-center justify-between mb-5 pt-1">
          <div>
            <h2 className="font-bold text-lg" style={{ color: D.heading }}>Collaborate with</h2>
            <p className="font-bold text-base" style={{ color: "#00AEEF" }}>@{video.username}</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center" style={{ background: D.xBg }}>
            <X className="w-4 h-4" style={{ color: D.xIcon }} />
          </button>
        </div>
        <p className="text-[11px] uppercase tracking-widest mb-3 font-semibold" style={{ color: D.label }}>Collaboration Type</p>
        <div className="grid grid-cols-4 gap-2 mb-6">
          {COLLAB_TYPES.map((t) => (
            <motion.button key={t.label} whileTap={{ scale: 0.93 }} onClick={() => setSelectedType(t.label)}
              className="flex flex-col items-center gap-1.5 py-3 rounded-2xl transition-colors"
              style={{
                background: selectedType === t.label ? "rgba(0,174,239,0.18)" : D.tileUnselBg,
                border: selectedType === t.label ? "1px solid rgba(0,174,239,0.55)" : D.tileUnselBorder,
              }}>
              <span className="text-xl leading-none">{t.icon}</span>
              <span className="text-[10px] font-semibold" style={{ color: selectedType === t.label ? "#00AEEF" : D.tileText }}>{t.label}</span>
            </motion.button>
          ))}
        </div>
        <p className="text-[11px] uppercase tracking-widest mb-3 font-semibold" style={{ color: D.label }}>Message</p>
        <textarea value={message} onChange={(e) => setMessage(e.target.value)}
          placeholder={`Hey @${video.username}, I'd love to collab on something 🔥`} rows={3}
          className="w-full rounded-2xl text-sm resize-none outline-none mb-5 p-4"
          style={{ background: D.areaBg, border: D.areaBorder, color: D.areaColor }} />
        <p className="text-[11px] uppercase tracking-widest mb-3 font-semibold" style={{ color: D.label }}>Budget</p>
        <div className="flex gap-2 flex-wrap mb-5">
          {BUDGETS.map((b) => (
            <button key={b} onClick={() => setBudget(b)} className="px-3 py-1.5 rounded-full text-[12px] font-semibold transition-colors"
              style={{ background: budget === b ? "rgba(0,174,239,0.2)" : D.chipUnsel, border: budget === b ? "1px solid rgba(0,174,239,0.55)" : D.chipBorder, color: budget === b ? "#00AEEF" : D.chipColor }}>
              {b}
            </button>
          ))}
        </div>
        <p className="text-[11px] uppercase tracking-widest mb-3 font-semibold" style={{ color: D.label }}>Timeline</p>
        <div className="flex gap-2 flex-wrap mb-7">
          {TIMELINES.map((t) => (
            <button key={t} onClick={() => setTimeline(t)} className="px-3 py-1.5 rounded-full text-[12px] font-semibold transition-colors"
              style={{ background: timeline === t ? "rgba(0,174,239,0.2)" : D.chipUnsel, border: timeline === t ? "1px solid rgba(0,174,239,0.55)" : D.chipBorder, color: timeline === t ? "#00AEEF" : D.chipColor }}>
              {t}
            </button>
          ))}
        </div>
        <motion.button whileTap={{ scale: 0.97 }} onClick={() => { if (!selectedType) return; setSent(true); setTimeout(() => { setSent(false); onClose(); }, 1800); }}
          disabled={!selectedType || sent}
          className="w-full py-4 rounded-2xl font-bold text-[15px] flex items-center justify-center gap-2 transition-opacity"
          style={{ background: selectedType ? "linear-gradient(135deg,#00AEEF,#0077cc)" : D.btnDisBg, color: selectedType ? "#fff" : D.btnDisColor, opacity: sent ? 0.9 : 1, boxShadow: selectedType ? "0 8px 24px rgba(0,174,239,0.35)" : "none" }}>
          <AnimatePresence mode="wait">
            {sent
              ? <motion.span key="sent" initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} className="flex items-center gap-2"><Check className="w-5 h-5" /> Request Sent!</motion.span>
              : <motion.span key="send" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2"><Send className="w-4 h-4" /> Send Collab Request</motion.span>
            }
          </AnimatePresence>
        </motion.button>
      </div>
    </motion.div>
  );
}

// ─── BOTTOM NAV ───────────────────────────────────────────────────────────────

function BottomNav({ active, onNav }: { active: string; onNav: (id: string) => void }) {
  const isDark = useTheme();
  const items = [
    { id: "home", icon: Home, label: "Feed" },
    { id: "search", icon: Search, label: "Discover" },
    { id: "create", icon: Plus, label: "" },
    { id: "inbox", icon: Mail, label: "Inbox" },
    { id: "profile", icon: User, label: "Profile" },
  ];
  const navBg = isDark
    ? "linear-gradient(to top, rgba(12,12,15,0.96) 60%, transparent)"
    : "linear-gradient(to top, rgba(242,245,251,0.97) 60%, transparent)";
  const inactiveColor = isDark ? "rgba(255,255,255,0.45)" : "rgba(10,14,26,0.35)";

  return (
    <nav className="absolute inset-x-0 bottom-0 z-20 flex items-end justify-around px-2 pt-3 pb-5"
      style={{ background: navBg, backdropFilter: "blur(12px)" }}>
      {items.map((item) => {
        const isCreate = item.id === "create";
        const isActive = item.id === active;
        const Icon = item.icon;
        return (
          <button key={item.id} onClick={() => onNav(item.id)} className="flex flex-col items-center gap-1 relative">
            {isCreate ? (
              <div className="w-11 h-8 rounded-xl flex items-center justify-center shadow-lg"
                style={{ background: "linear-gradient(135deg,#00AEEF,#0077cc)", boxShadow: "0 4px 16px rgba(0,174,239,0.4)" }}>
                <Plus className="w-5 h-5 text-white" strokeWidth={2.5} />
              </div>
            ) : (
              <>
                <Icon className="w-6 h-6 transition-colors"
                  style={{ color: isActive ? "#00AEEF" : inactiveColor }}
                  strokeWidth={isActive ? 2.2 : 1.8} />
                {item.label && (
                  <span className="text-[10px] font-semibold" style={{ color: isActive ? "#00AEEF" : inactiveColor }}>
                    {item.label}
                  </span>
                )}
                {isActive && (
                  <motion.div layoutId="nav-dot" className="absolute -bottom-2 w-1 h-1 rounded-full" style={{ background: "#00AEEF" }} />
                )}
              </>
            )}
          </button>
        );
      })}
    </nav>
  );
}

// ─── APP ──────────────────────────────────────────────────────────────────────

// Full-screen app surface — fills the whole viewport edge-to-edge on every screen size.
const FRAME = "relative overflow-hidden w-full h-full";

export default function App() {
  // The signed-in account, restored from the persisted session on load.
  const [account, setAccount] = useState<Account | null>(() => getSession());
  const [isDark, setIsDark] = useState(true);
  const [screen, setScreen] = useState<"feed" | "discover" | "profile" | "inbox">("feed");
  const [feedTab, setFeedTab] = useState<"forYou" | "following">("forYou");
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [liveMode, setLiveMode] = useState<"off" | "setup" | "creator" | "viewer" | "upload">("off");
  const [liveTitle, setLiveTitle] = useState("");
  const [idx, setIdx] = useState(0);
  const [dir, setDir] = useState(1);
  const [collabTarget, setCollabTarget] = useState<DisplayVideo | null>(null);
  const [commentTarget, setCommentTarget] = useState<DisplayVideo | null>(null);
  const [shareTarget, setShareTarget] = useState<DisplayVideo | null>(null);
  const [commentCountOverrides, setCommentCountOverrides] = useState<Map<string, number>>(() => new Map());
  const [paused, setPaused] = useState(false);
  const touchStartY = useRef(0);
  const pausedRef = useRef(paused);

  const { items: feedItems, status: feedStatus, error: feedError, loadMore, reachedEnd, reload } = useFeed(feedTab === "following");

  useEffect(() => {
    activatePosts(account?.email ?? null);
    activateFollowGraph(account?.email ?? null);
    activateLikeGraph(account?.email ?? null);
    activateSaveGraph(account?.email ?? null);
    activateShareGraph(account?.email ?? null);
  }, [account?.email]);

  // The two top-bar tabs are the same feed filtered, so switching them restarts
  // at the first video rather than leaving `idx` past the end of a shorter list.
  const displayItems = useMemo(() => feedItems.map(toDisplayVideo), [feedItems]);
  const feed = displayItems;
  const video = feed[Math.min(idx, feed.length - 1)];

  useEffect(() => { pausedRef.current = paused; }, [paused]);

  useEffect(() => {
    if (!video) return;
    const watch = { seconds: 0, lastTick: Date.now() };
    const timer = window.setInterval(() => {
      const now = Date.now();
      if (!pausedRef.current) watch.seconds += (now - watch.lastTick) / 1000;
      watch.lastTick = now;
    }, 1000);
    return () => {
      window.clearInterval(timer);
      const duration = video.durationSec ?? 0;
      void trackPostWatch(video.id, watch.seconds, duration > 0 && watch.seconds >= duration);
    };
  }, [video?.id]);

  const likeState = useLike(video?.id ?? "", video?.isLiked ?? false, video?.likes ?? 0);
  const saveState = useSave(video?.id ?? "", video?.isSaved ?? false, video?.saves ?? 0);
  const shareState = useShare(video?.id ?? "", video?.shares ?? 0);
  const commentCount = video ? commentCountOverrides.get(video.id) ?? video.comments : 0;
  const changeCommentCount = useCallback((postId: string, delta: number) => {
    setCommentCountOverrides((previous) => {
      const next = new Map(previous);
      next.set(postId, Math.max(0, (next.get(postId) ?? feed.find((item) => item.id === postId)?.comments ?? 0) + delta));
      return next;
    });
  }, [feed]);

  // A page after the next is requested a slide before it is needed, so
  // scrolling never waits on a page that is still in flight.
  useEffect(() => {
    if (!reachedEnd && idx >= feed.length - 2) loadMore();
  }, [idx, feed.length, reachedEnd, loadMore]);

  const goNext = useCallback(() => { if (idx < feed.length - 1) { setDir(1); setIdx((i) => i + 1); } }, [idx, feed.length]);
  const goPrev = useCallback(() => { if (idx > 0) { setDir(-1); setIdx((i) => i - 1); } }, [idx]);

  const switchTab = useCallback((tab: "forYou" | "following") => {
    setFeedTab((current) => {
      if (current !== tab) { setDir(1); setIdx(0); }
      return tab;
    });
  }, []);

  const wheelLocked = useRef(false);
  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (wheelLocked.current) return;
    if (Math.abs(e.deltaY) < 20) return;
    wheelLocked.current = true;
    e.deltaY > 0 ? goNext() : goPrev();
    setTimeout(() => { wheelLocked.current = false; }, 700);
  }, [goNext, goPrev]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") goNext();
      if (e.key === "ArrowUp") goPrev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goNext, goPrev]);

  const handleLogout = useCallback(() => { endSession(); setAccount(null); setScreen("feed"); }, []);
  // `deleteAccount` has already ended the session by the time this runs.
  const handleDeleted = useCallback(() => { setShowDeleteModal(false); setAccount(null); setScreen("feed"); }, []);

  if (!account) {
    return (
      <ThemeContext.Provider value={true}>
        <div className="h-[100dvh] w-full overflow-hidden" style={{ background: "#000" }}>
          <div className={`${FRAME} bg-black`}>
            <AuthFlow onAuthenticated={setAccount} />
          </div>
        </div>
      </ThemeContext.Provider>
    );
  }

  return (
    <ThemeContext.Provider value={isDark}>
      <div
        className={`h-[100dvh] w-full overflow-hidden${isDark ? " dark" : ""}`}
        style={{ background: isDark ? "#000" : "#f2f5fb", fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif" }}
      >
        <div
          className={FRAME}
          style={{ background: isDark ? "#000" : "#f2f5fb" }}
          onTouchStart={(e) => { touchStartY.current = e.touches[0].clientY; }}
          onTouchEnd={(e) => { const d = touchStartY.current - e.changedTouches[0].clientY; if (Math.abs(d) > 45) d > 0 ? goNext() : goPrev(); }}
          onWheel={handleWheel}
        >
          {/* ── Video slides ── */}
          {video ? (
          <AnimatePresence initial={false} custom={dir} mode="wait">
            <motion.div key={video.id} custom={dir}
              initial={{ y: dir > 0 ? "100%" : "-100%", opacity: 0.4 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: dir > 0 ? "-100%" : "100%", opacity: 0.4 }}
              transition={{ type: "spring", damping: 38, stiffness: 340 }}
              className="absolute inset-0"
              onClick={() => setPaused((p) => !p)}
            >
              <div className="absolute inset-0 bg-cover bg-center" style={{ backgroundImage: `url(${video.thumbnail})` }} />

              {/* Uploaded video background — image-only posts fall back to the thumbnail above */}
              {video.mediaUrl && (
                <PostVideo
                  mediaUrl={video.mediaUrl}
                  isActive={idx === feed.findIndex((v) => v.id === video.id)}
                  paused={paused}
                />
              )}

              <div className="absolute inset-0" style={{ background: "linear-gradient(to bottom,rgba(0,0,0,0.25) 0%,transparent 25%,transparent 55%,rgba(0,0,0,0.65) 80%,rgba(0,0,0,0.85) 100%)" }} />

              {/* Top bar */}
              <div className="absolute top-0 inset-x-0 flex items-center justify-between px-5 pt-12 z-10"
                onClick={(e) => e.stopPropagation()}>
                <div className="flex gap-5">
                  {([
                    { id: "following", label: "Following" },
                    { id: "forYou", label: "For You" },
                  ] as const).map((tab) => (
                    <div key={tab.id} className="flex flex-col items-center">
                      <button onClick={() => switchTab(tab.id)}
                        className="text-[14px]"
                        style={{
                          color: feedTab === tab.id ? "#fff" : "rgba(255,255,255,0.6)",
                          fontWeight: feedTab === tab.id ? 700 : 600,
                        }}>
                        {tab.label}
                      </button>
                      {feedTab === tab.id && (
                        <motion.div layoutId="feed-tab-underline" className="w-5 h-0.5 rounded-full mt-0.5"
                          style={{ background: "#00AEEF" }} />
                      )}
                    </div>
                  ))}
                </div>
                <button onClick={() => setScreen("discover")} aria-label="Search"
                  className="w-8 h-8 rounded-full flex items-center justify-center"
                  style={{ background: "rgba(255,255,255,0.1)", backdropFilter: "blur(8px)" }}>
                  <Search className="w-4 h-4 text-white" />
                </button>
              </div>

              <AnimatePresence>
                {paused && (
                  <motion.div initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }}
                    className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
                    <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(8px)" }}>
                      <div className="flex gap-1.5"><div className="w-1.5 h-7 rounded-full bg-white" /><div className="w-1.5 h-7 rounded-full bg-white" /></div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {idx < feed.length - 1 && (
                <div className="absolute bottom-36 right-1/2 translate-x-1/2 z-10 flex flex-col items-center gap-0.5 opacity-40">
                  <ChevronUp className="w-4 h-4 text-white" />
                </div>
              )}

              <ActionRail video={video} liked={likeState.liked} likeCount={likeState.likes}
                saved={saveState.saved} saveCount={saveState.saves}
                commentCount={commentCount}
                onLike={() => { void likeState.toggle(); }}
                onSave={() => { void saveState.toggle(); }}
                onCollab={() => setCollabTarget(video)}
                onComment={() => setCommentTarget(video)}
                onShare={() => setShareTarget(video)} shareCount={shareState.shares} />

              <VideoInfo video={video} />

              {/* Progress dots */}
              <div className="absolute right-4 top-1/2 -translate-y-1/2 flex flex-col gap-1.5 z-10">
                {feed.map((v, i) => (
                  <button key={v.id}
                    onClick={(e) => { e.stopPropagation(); setDir(i > idx ? 1 : -1); setIdx(i); }}
                    className="rounded-full transition-all"
                    style={{ width: 3, height: i === idx ? 20 : 6, background: i === idx ? "#00AEEF" : "rgba(255,255,255,0.3)" }} />
                ))}
              </div>
            </motion.div>
          </AnimatePresence>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center px-8 text-center">
              {feedStatus === "error" ? (
                <div className="flex flex-col items-center gap-3">
                  <span className="text-white/80 text-sm">{feedError}</span>
                  <button onClick={reload}
                    className="px-4 py-2 rounded-full text-xs font-semibold text-white"
                    style={{ background: "rgba(255,255,255,0.12)" }}>
                    Retry
                  </button>
                </div>
              ) : feedStatus === "loading" ? (
                <span className="text-white/60 text-sm">Loading feed…</span>
              ) : (
                <span className="text-white/60 text-sm">No posts yet.</span>
              )}
            </div>
          )}


          {/* ── Bottom nav ── */}
          <BottomNav
            active={screen === "discover" ? "search" : screen === "profile" ? "profile" : screen === "inbox" ? "inbox" : "home"}
            onNav={(id) => {
              if (id === "search") setScreen("discover");
              else if (id === "profile") setScreen("profile");
              else if (id === "inbox") setScreen("inbox");
              else if (id === "create") setLiveMode("upload");
              else setScreen("feed");
            }}
          />

          {/* ── Trending Sounds ── */}
          <AnimatePresence>
            {screen === "discover" && <TrendingSounds key="sounds" onBack={() => setScreen("feed")} />}
          </AnimatePresence>

          {/* ── Inbox ── */}
          <AnimatePresence>
            {screen === "inbox" && (
              <motion.div key="inbox" initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", damping: 34, stiffness: 300 }}>
                <InboxScreen onBack={() => setScreen("feed")} />
              </motion.div>
            )}
          </AnimatePresence>

          {/* ── Profile / Settings ── */}
          <AnimatePresence>
            {screen === "profile" && (
              <motion.div key="settings" initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", damping: 34, stiffness: 300 }}>
                <SettingsScreen
                  account={account}
                  onBack={() => setScreen("feed")}
                  onLogout={handleLogout}
                  onDeleteProfile={() => setShowDeleteModal(true)}
                  onAccountChange={setAccount}
                  isDark={isDark}
                  onToggleTheme={() => setIsDark((d) => !d)}
                />
              </motion.div>
            )}
          </AnimatePresence>

          {/* ── Delete profile modal ── */}
          <AnimatePresence>
            {showDeleteModal && (
              <>
                <motion.div key="del-bd" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                  className="absolute inset-0 z-40" style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(6px)" }}
                  onClick={() => setShowDeleteModal(false)} />
                <DeleteProfileModal key="del-modal" account={account} onDeleted={handleDeleted} onCancel={() => setShowDeleteModal(false)} />
              </>
            )}
          </AnimatePresence>

          {/* ── Comment sheet ── */}
          <AnimatePresence>
            {commentTarget && (
              <>
                <motion.div key="comment-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
                  className="absolute inset-0 z-40" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
                  onClick={() => setCommentTarget(null)} />
                <CommentSheet key="comment-sheet" video={commentTarget} onClose={() => setCommentTarget(null)}
                  onCommentCountChange={(delta) => changeCommentCount(commentTarget.id, delta)} />
              </>
            )}
          </AnimatePresence>

          {/* ── Share sheet ── */}
          <AnimatePresence>
            {shareTarget && (
              <>
                <motion.div key="share-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
                  className="absolute inset-0 z-40" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
                  onClick={() => setShareTarget(null)} />
                <ShareSheet key="share-sheet" video={shareTarget} onShared={() => { void shareState.share(); }} onClose={() => setShareTarget(null)} />
              </>
            )}
          </AnimatePresence>

          {/* ── Collab sheet ── */}
          <AnimatePresence>
            {collabTarget && (
              <>
                <motion.div key="backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
                  className="absolute inset-0 z-40" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
                  onClick={() => setCollabTarget(null)} />
                <CollabSheet key="sheet" video={collabTarget} onClose={() => setCollabTarget(null)} />
              </>
            )}
          </AnimatePresence>

          <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 bottom-24 flex-col items-center gap-2 z-30 pointer-events-none opacity-30">
            <ChevronUp className="w-5 h-5 text-white" />
            <ChevronDown className="w-5 h-5 text-white" />
          </div>

          {/* ── Live banner strip ── */}
          {screen === "feed" && liveMode === "off" && (
            <LiveBannerStrip onCreate={() => setLiveMode("setup")} onWatch={() => setLiveMode("viewer")} />
          )}

          <AnimatePresence>
            {liveMode === "upload" && (
              <UploadScreen key="upload" onClose={() => setLiveMode("off")} />
            )}
          </AnimatePresence>

          <AnimatePresence>
            {liveMode === "setup" && (
              <GoLiveSetup key="live-setup" onStart={(title) => { setLiveTitle(title); setLiveMode("creator"); }} onClose={() => setLiveMode("off")} />
            )}
          </AnimatePresence>

          <AnimatePresence>
            {liveMode === "creator" && (
              <CreatorLiveView key="creator-live" title={liveTitle} onEnd={() => setLiveMode("off")} />
            )}
          </AnimatePresence>

          <AnimatePresence>
            {liveMode === "viewer" && (
              <ViewerLiveView key="viewer-live"
                creator={{ name: "nova.dj", avatar: "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=60&h=60&fit=crop&auto=format", title: "HYPERSONIC studio session 🔊" }}
                onClose={() => setLiveMode("off")} />
            )}
          </AnimatePresence>
        </div>
      </div>
    </ThemeContext.Provider>
  );
}
