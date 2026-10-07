import { useEffect, useState } from "react";
import { useTheme } from "./ThemeContext";
import { motion, AnimatePresence } from "motion/react";
import {
  ArrowLeft, Bookmark, TrendingUp, TrendingDown,
  Play, Music2, Share2, ChevronRight, Mic2,
  X, Heart, Navigation, Check, Send
} from "lucide-react";
import { fetchTrendingSounds } from "./profile-graphql";
import { PaidDiscoveryLink } from "./PaidDiscovery";

// ─── DATA ─────────────────────────────────────────────────────────────────────

export interface Sound {
  id: string;
  title: string;
  creator: string;
  creatorAvatar: string;
  artwork: string;
  genre: string;
  videoCount: number;
  totalPlays: number;
  rank: number;
  growthPct: number;
  duration: string;
  bpm: number;
}

interface SoundVideo {
  id: string;
  thumb: string;
  user: string;
  userAvatar: string;
  views: number;
  likes: number;
  caption: string;
  collabScore: number;
  collabOpen: boolean;
}

export const SOUNDS: Sound[] = [
  {
    id: "s1", title: "Midnight Drive", creator: "nova.dj",
    creatorAvatar: "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=300&h=300&fit=crop&auto=format",
    genre: "Electronic", videoCount: 284700, totalPlays: 18400000, rank: 1, growthPct: 142, duration: "0:45", bpm: 128,
  },
  {
    id: "s2", title: "golden hour", creator: "JVKE",
    creatorAvatar: "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=300&h=300&fit=crop&auto=format",
    genre: "Pop", videoCount: 531200, totalPlays: 42100000, rank: 2, growthPct: 89, duration: "0:30", bpm: 95,
  },
  {
    id: "s3", title: "HYPERSONIC", creator: "nova.dj",
    creatorAvatar: "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=300&h=300&fit=crop&auto=format",
    genre: "Electronic", videoCount: 892400, totalPlays: 67300000, rank: 3, growthPct: 67, duration: "1:00", bpm: 140,
  },
  {
    id: "s4", title: "Lo-Fi Study Session", creator: "lofi.luna",
    creatorAvatar: "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=300&h=300&fit=crop&auto=format",
    genre: "Lo-Fi", videoCount: 127600, totalPlays: 9800000, rank: 4, growthPct: 54, duration: "0:60", bpm: 75,
  },
  {
    id: "s5", title: "Original Sound", creator: "zara.creates",
    creatorAvatar: "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1598488035139-bdbb2231ce04?w=300&h=300&fit=crop&auto=format",
    genre: "R&B", videoCount: 94300, totalPlays: 7200000, rank: 5, growthPct: 38, duration: "0:22", bpm: 90,
  },
  {
    id: "s6", title: "Cinematic Rise", creator: "ren.filmco",
    creatorAvatar: "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1540569876291-7b03b5441327?w=300&h=300&fit=crop&auto=format",
    genre: "Cinematic", videoCount: 63100, totalPlays: 4900000, rank: 6, growthPct: 22, duration: "0:55", bpm: 82,
  },
  {
    id: "s7", title: "Bass Heavy", creator: "beatsby.kai",
    creatorAvatar: "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1516280440614-37939bbacd81?w=300&h=300&fit=crop&auto=format",
    genre: "Hip-Hop", videoCount: 41800, totalPlays: 3100000, rank: 7, growthPct: -8, duration: "0:38", bpm: 98,
  },
  {
    id: "s8", title: "Viral Dance Remix", creator: "freq.faye",
    creatorAvatar: "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=60&h=60&fit=crop&auto=format",
    artwork: "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=300&h=300&fit=crop&auto=format",
    genre: "Dance", videoCount: 38400, totalPlays: 2700000, rank: 8, growthPct: -14, duration: "0:42", bpm: 120,
  },
];

const SOUND_VIDEOS: SoundVideo[] = [
  { id: "v1", thumb: "https://images.unsplash.com/photo-1598488035139-bdbb2231ce04?w=400&h=711&fit=crop&auto=format", user: "zara.creates", userAvatar: "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=60&h=60&fit=crop&auto=format", views: 284700, likes: 48200, caption: "Late night studio sessions always hit different 🎵", collabScore: 4.9, collabOpen: true },
  { id: "v2", thumb: "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=400&h=711&fit=crop&auto=format", user: "lex.codes", userAvatar: "https://images.unsplash.com/photo-1539571696357-5a69c17a67c6?w=60&h=60&fit=crop&auto=format", views: 127600, likes: 21300, caption: "Built this entire app in a weekend ⚡", collabScore: 4.5, collabOpen: true },
  { id: "v3", thumb: "https://images.unsplash.com/photo-1516280440614-37939bbacd81?w=400&h=711&fit=crop&auto=format", user: "nova.dj", userAvatar: "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=60&h=60&fit=crop&auto=format", views: 892400, likes: 143000, caption: "The drop at 2:14 will change your life 🔊", collabScore: 4.8, collabOpen: true },
  { id: "v4", thumb: "https://images.unsplash.com/photo-1540569876291-7b03b5441327?w=400&h=711&fit=crop&auto=format", user: "ren.filmco", userAvatar: "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=60&h=60&fit=crop&auto=format", views: 344900, likes: 67800, caption: "Shot this on a $200 camera 🎬", collabScore: 4.6, collabOpen: false },
  { id: "v5", thumb: "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=400&h=711&fit=crop&auto=format", user: "milo.visuals", userAvatar: "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=60&h=60&fit=crop&auto=format", views: 531200, likes: 92400, caption: "Golden hour never misses 📸", collabScore: 4.7, collabOpen: true },
  { id: "v6", thumb: "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=400&h=711&fit=crop&auto=format", user: "drop.dani", userAvatar: "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=60&h=60&fit=crop&auto=format", views: 98300, likes: 14600, caption: "This sound goes crazy with this transition ✨", collabScore: 4.3, collabOpen: true },
];

const GENRES = ["All", "Electronic", "Pop", "Hip-Hop", "Lo-Fi", "R&B", "Cinematic", "Dance"];
const COLLAB_TYPES = ["🎵 Music", "📹 Video", "🎙 Podcast", "📈 Business", "📸 Photo", "🎮 Gaming", "💼 Brand Deal", "✨ Custom"];
const SHARE_PLATFORMS = [
  { id: "ig",  label: "Instagram", bg: "linear-gradient(135deg,#f09433,#dc2743,#bc1888)", icon: "IG" },
  { id: "x",   label: "X",         bg: "#000",    icon: "𝕏" },
  { id: "wa",  label: "WhatsApp",  bg: "#25D366", icon: "W" },
  { id: "tt",  label: "TikTok",    bg: "#010101", icon: "TK" },
  { id: "sc",  label: "Snapchat",  bg: "#FFFC00", icon: "👻", dark: true },
  { id: "tg",  label: "Telegram",  bg: "#229ED9", icon: "✈" },
];

const fmtNum = (n: number) =>
  n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M"
  : n >= 1_000   ? (n / 1_000).toFixed(1) + "K"
  : String(n);

// ─── COLLAB SHEET (inline, lightweight) ──────────────────────────────────────

function CollabSheet({ username, onClose }: { username: string; onClose: () => void }) {
  const isDark = useTheme();
  const [type, setType] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const sheetBg = isDark ? "#1a1a22" : "#ffffff";
  const sheetBorder = isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)";
  const handleColor = isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.12)";
  const headingColor = isDark ? "#fff" : "#0a0e1a";
  const labelColor = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const typeItemBg = (selected: boolean) => selected ? "rgba(0,174,239,0.18)" : isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)";
  const typeItemBorder = (selected: boolean) => selected ? "1px solid rgba(0,174,239,0.5)" : isDark ? "1px solid rgba(255,255,255,0.06)" : "1px solid rgba(0,0,0,0.07)";
  const typeTextColor = isDark ? "rgba(255,255,255,0.7)" : "rgba(10,14,26,0.6)";
  const closeBtnBg = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.05)";
  const closeIconColor = isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.4)";

  const send = () => {
    if (!type) return;
    setSent(true);
    setTimeout(() => { setSent(false); onClose(); }, 1600);
  };

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 320 }}
      className="absolute inset-x-0 bottom-0 z-50 rounded-t-3xl overflow-hidden"
      style={{ background: sheetBg, border: sheetBorder, borderBottom: "none", maxHeight: "80%", boxShadow: "0 -20px 60px rgba(0,0,0,0.35)" }}
    >
      <div className="flex justify-center pt-3 pb-2"><div className="w-9 h-1 rounded-full" style={{ background: handleColor }} /></div>
      <div className="px-5 pb-8 overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="font-bold text-[16px]" style={{ color: headingColor }}>Collaborate with</p>
            <p className="font-bold text-[14px]" style={{ color: "#00AEEF" }}>@{username}</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center" style={{ background: closeBtnBg }}>
            <X className="w-4 h-4" style={{ color: closeIconColor }} />
          </button>
        </div>
        <p className="text-[10px] uppercase tracking-widest mb-3 font-semibold" style={{ color: labelColor }}>Collaboration Type</p>
        <div className="grid grid-cols-4 gap-2 mb-5">
          {COLLAB_TYPES.map((t) => (
            <button key={t} onClick={() => setType(t)}
              className="flex flex-col items-center gap-1 py-2.5 rounded-xl text-center"
              style={{ background: typeItemBg(type === t), border: typeItemBorder(type === t) }}
            >
              <span className="text-lg">{t.split(" ")[0]}</span>
              <span className="text-[10px] font-semibold" style={{ color: typeTextColor }}>{t.split(" ").slice(1).join(" ")}</span>
            </button>
          ))}
        </div>
        <motion.button whileTap={{ scale: 0.97 }} onClick={send} disabled={!type || sent}
          className="w-full py-3.5 rounded-2xl font-bold text-[14px] flex items-center justify-center gap-2"
          style={{ background: type ? "linear-gradient(135deg,#00AEEF,#0077cc)" : isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)", color: type ? "#fff" : isDark ? "rgba(255,255,255,0.25)" : "rgba(10,14,26,0.25)", boxShadow: type ? "0 6px 20px rgba(0,174,239,0.3)" : "none" }}
        >
          <AnimatePresence mode="wait">
            {sent
              ? <motion.span key="ok" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2"><Check className="w-4 h-4" /> Sent!</motion.span>
              : <motion.span key="go" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-2"><Send className="w-4 h-4" /> Send Request</motion.span>
            }
          </AnimatePresence>
        </motion.button>
      </div>
    </motion.div>
  );
}

// ─── SHARE SHEET ─────────────────────────────────────────────────────────────

function SoundShareSheet({ title, onClose }: { title: string; onClose: () => void }) {
  const isDark = useTheme();
  const [sharedTo, setSharedTo] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const url = `https://connexionz.app/sound/${title.toLowerCase().replace(/\s+/g, "-")}`;

  const sheetBg = isDark ? "#1a1a22" : "#ffffff";
  const sheetBorder = isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)";
  const handleColor = isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.12)";
  const headingColor = isDark ? "#fff" : "#0a0e1a";
  const subtitleColor = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const platformLabelColor = isDark ? "rgba(255,255,255,0.5)" : "rgba(10,14,26,0.45)";
  const urlBarBg = isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)";
  const urlBarBorder = isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)";
  const urlTextColor = isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.35)";

  const copy = () => {
    navigator.clipboard.writeText(url).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 320 }}
      className="absolute inset-x-0 bottom-0 z-50 rounded-t-3xl"
      style={{ background: sheetBg, border: sheetBorder, borderBottom: "none", boxShadow: "0 -20px 60px rgba(0,0,0,0.35)" }}
    >
      <div className="flex justify-center pt-3 pb-2"><div className="w-9 h-1 rounded-full" style={{ background: handleColor }} /></div>
      <div className="px-5 pb-8">
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="font-bold text-[15px]" style={{ color: headingColor }}>Share Sound</p>
            <p className="text-[12px]" style={{ color: subtitleColor }}>{title}</p>
          </div>
          <button onClick={onClose} className="w-7 h-7 rounded-full flex items-center justify-center" style={{ background: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.05)" }}>
            <X className="w-3.5 h-3.5" style={{ color: isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.4)" }} />
          </button>
        </div>
        <div className="grid grid-cols-6 gap-3 mb-5">
          {SHARE_PLATFORMS.map((p) => (
            <motion.button key={p.id} whileTap={{ scale: 0.88 }} onClick={() => { setSharedTo(p.id); setTimeout(() => setSharedTo(null), 1200); }}
              className="flex flex-col items-center gap-1.5"
            >
              <div className="w-12 h-12 rounded-xl flex items-center justify-center font-bold text-sm relative overflow-hidden"
                style={{ background: p.bg, color: (p as any).dark ? "#000" : "#fff", boxShadow: "0 4px 12px rgba(0,0,0,0.3)" }}>
                {sharedTo === p.id
                  ? <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.4)" }}><Check className="w-5 h-5 text-white" /></motion.div>
                  : <span className="select-none">{p.icon}</span>}
              </div>
              <span className="text-[9px] font-medium" style={{ color: platformLabelColor }}>{p.label}</span>
            </motion.button>
          ))}
        </div>
        <div className="flex items-center gap-3 rounded-2xl px-4 py-3" style={{ background: urlBarBg, border: urlBarBorder }}>
          <span className="flex-1 text-[11px] truncate" style={{ color: urlTextColor }}>{url}</span>
          <button onClick={copy} className="px-3 py-1.5 rounded-xl text-[11px] font-bold flex-shrink-0"
            style={{ background: "rgba(0,174,239,0.15)", border: "1px solid rgba(0,174,239,0.35)", color: "#00AEEF" }}>
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
      </div>
    </motion.div>
  );
}

// ─── CREATOR PROFILE SHEET ───────────────────────────────────────────────────

function CreatorSheet({ video, onCollab, onClose }: { video: SoundVideo; onCollab: () => void; onClose: () => void }) {
  const isDark = useTheme();

  const sheetBg = isDark ? "#1a1a22" : "#ffffff";
  const sheetBorder = isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)";
  const handleColor = isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.12)";
  const usernameColor = isDark ? "#fff" : "#0a0e1a";
  const subtextColor = isDark ? "rgba(255,255,255,0.3)" : "rgba(10,14,26,0.35)";
  const statBg = isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)";
  const statValueColor = isDark ? "#fff" : "#0a0e1a";
  const statLabelColor = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const captionColor = isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.55)";
  const secondaryBtnBg = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)";
  const secondaryBtnBorder = isDark ? "1px solid rgba(255,255,255,0.1)" : "1px solid rgba(0,0,0,0.08)";
  const secondaryBtnColor = isDark ? "rgba(255,255,255,0.7)" : "rgba(10,14,26,0.6)";
  const closeBtnBg = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.05)";

  return (
    <motion.div
      initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 320 }}
      className="absolute inset-x-0 bottom-0 z-50 rounded-t-3xl"
      style={{ background: sheetBg, border: sheetBorder, borderBottom: "none", boxShadow: "0 -20px 60px rgba(0,0,0,0.35)" }}
    >
      <div className="flex justify-center pt-3 pb-3"><div className="w-9 h-1 rounded-full" style={{ background: handleColor }} /></div>
      <div className="px-5 pb-8">
        <div className="flex items-center gap-4 mb-5">
          <img src={video.userAvatar} alt={video.user} className="w-16 h-16 rounded-full object-cover border-2" style={{ borderColor: "#00AEEF" }} />
          <div className="flex-1">
            <p className="font-bold text-[16px]" style={{ color: usernameColor }}>@{video.user}</p>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="text-[11px] font-bold" style={{ color: "#00AEEF" }}>⭐ {video.collabScore.toFixed(1)}</span>
              <span className="text-[11px]" style={{ color: subtextColor }}>Collab Score</span>
            </div>
            {video.collabOpen
              ? <span className="inline-block mt-1 text-[10px] font-semibold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,174,239,0.15)", border: "1px solid rgba(0,174,239,0.4)", color: "#00AEEF" }}>✦ Open to Collab</span>
              : <span className="inline-block mt-1 text-[10px] font-semibold px-2 py-0.5 rounded-full" style={{ background: isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)", color: subtextColor }}>Not accepting</span>
            }
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center self-start" style={{ background: closeBtnBg }}>
            <X className="w-3.5 h-3.5" style={{ color: isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.4)" }} />
          </button>
        </div>

        <div className="grid grid-cols-3 gap-2 mb-5">
          {[
            { label: "Views", value: fmtNum(video.views) },
            { label: "Likes", value: fmtNum(video.likes) },
            { label: "Collabs", value: Math.floor(video.collabScore * 40) },
          ].map((s) => (
            <div key={s.label} className="flex flex-col items-center py-3 rounded-xl" style={{ background: statBg }}>
              <span className="font-bold text-[16px]" style={{ color: statValueColor }}>{s.value}</span>
              <span className="text-[11px]" style={{ color: statLabelColor }}>{s.label}</span>
            </div>
          ))}
        </div>

        <p className="text-[13px] leading-snug mb-5 px-1" style={{ color: captionColor }}>"{video.caption}"</p>

        <div className="flex gap-3">
          <motion.button whileTap={{ scale: 0.96 }} onClick={() => { onCollab(); onClose(); }}
            className="flex-1 py-3.5 rounded-2xl font-bold text-[14px] flex items-center justify-center gap-2"
            style={{ background: "linear-gradient(135deg,#00AEEF,#0077cc)", color: "#fff", boxShadow: "0 6px 20px rgba(0,174,239,0.3)" }}
          >
            <span className="font-bold text-[15px]">C</span> Collab
          </motion.button>
          <button className="flex-1 py-3.5 rounded-2xl font-bold text-[14px] flex items-center justify-center"
            style={{ background: secondaryBtnBg, border: secondaryBtnBorder, color: secondaryBtnColor }}>
            View Profile
          </button>
        </div>
      </div>
    </motion.div>
  );
}

// ─── FULL-SCREEN VIDEO VIEWER ─────────────────────────────────────────────────

function VideoViewer({
  video, sound, onClose,
}: { video: SoundVideo; sound: Sound; onClose: () => void }) {
  const [liked, setLiked] = useState(false);
  const [paused, setPaused] = useState(false);
  const [sheet, setSheet] = useState<"none" | "collab" | "creator">("none");

  return (
    <motion.div
      initial={{ opacity: 0, scale: 1.04 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.22 }}
      className="absolute inset-0 z-40 bg-black"
    >
      {/* Background */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: `url(${video.thumb})` }}
        onClick={() => setPaused((p) => !p)}
      />
      <div className="absolute inset-0" style={{ background: "linear-gradient(to bottom,rgba(0,0,0,0.3) 0%,transparent 30%,transparent 55%,rgba(0,0,0,0.8) 100%)" }} />

      {/* Pause indicator */}
      <AnimatePresence>
        {paused && (
          <motion.div initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }}
            className="absolute inset-0 flex items-center justify-center pointer-events-none z-10"
          >
            <div className="w-16 h-16 rounded-full flex items-center justify-center gap-1.5" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(8px)" }}>
              <div className="w-1.5 h-7 rounded-full bg-white" />
              <div className="w-1.5 h-7 rounded-full bg-white" />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Close */}
      <button onClick={onClose} className="absolute top-12 left-4 w-9 h-9 rounded-full flex items-center justify-center z-10"
        style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(8px)" }}>
        <X className="w-4 h-4 text-white" />
      </button>

      {/* Sound badge */}
      <div className="absolute top-12 left-1/2 -translate-x-1/2 flex items-center gap-2 px-3 py-1.5 rounded-full z-10"
        style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,0.12)" }}>
        <Music2 className="w-3 h-3" style={{ color: "#00AEEF" }} />
        <span className="text-white text-[11px] font-semibold">{sound.title}</span>
      </div>

      {/* Right rail */}
      <div className="absolute right-3 bottom-32 z-10 flex flex-col items-center gap-5">
        {/* Creator */}
        <button onClick={() => setSheet("creator")} className="flex flex-col items-center gap-1">
          <img src={video.userAvatar} alt={video.user} className="w-11 h-11 rounded-full object-cover border-2 border-white" />
          <div className="w-5 h-5 -mt-3 rounded-full flex items-center justify-center text-white text-xs font-bold"
            style={{ background: "#00AEEF", boxShadow: "0 2px 8px rgba(0,174,239,0.5)" }}>+</div>
        </button>

        {/* Like */}
        <motion.button whileTap={{ scale: 0.85 }} onClick={() => setLiked((l) => !l)} className="flex flex-col items-center gap-1">
          <motion.div animate={liked ? { scale: [1, 1.35, 1] } : {}} transition={{ duration: 0.25 }}>
            <Heart className={`w-7 h-7 ${liked ? "fill-red-500 text-red-500" : "text-white"}`} />
          </motion.div>
          <span className="text-white text-[11px] font-semibold">{fmtNum(video.likes + (liked ? 1 : 0))}</span>
        </motion.button>

        {/* Collab */}
        <button onClick={() => setSheet("collab")} className="flex flex-col items-center gap-1.5">
          <div className="w-12 h-12 rounded-full flex items-center justify-center"
            style={{ background: "linear-gradient(135deg,#00AEEF,#0077cc)", boxShadow: "0 0 20px rgba(0,174,239,0.45)" }}>
            <span className="text-white font-bold text-xl">C</span>
          </div>
          <span className="text-white/80 text-[10px] font-semibold uppercase tracking-wide">Collab</span>
        </button>

        {/* Share */}
        <motion.button whileTap={{ scale: 0.85 }} className="flex flex-col items-center gap-1">
          <Navigation className="w-7 h-7 text-white" />
          <span className="text-white text-[11px] font-semibold">{fmtNum(Math.floor(video.views * 0.04))}</span>
        </motion.button>
      </div>

      {/* Bottom info */}
      <div className="absolute left-4 bottom-28 right-20 z-10 space-y-1.5">
        <button onClick={() => setSheet("creator")} className="flex items-center gap-2">
          <span className="text-white font-bold text-[15px]">@{video.user}</span>
          {video.collabOpen && (
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,174,239,0.18)", border: "1px solid rgba(0,174,239,0.5)", color: "#00AEEF" }}>✦ Open to Collab</span>
          )}
        </button>
        <p className="text-white/85 text-[13px] leading-snug">{video.caption}</p>
        <div className="flex items-center gap-1.5 pt-0.5">
          <span className="text-[11px] font-bold" style={{ color: "#00AEEF" }}>⭐ {video.collabScore.toFixed(1)}</span>
          <span className="text-white/35 text-[11px]">Collab Score</span>
        </div>
      </div>

      {/* Sheets */}
      <AnimatePresence>
        {sheet !== "none" && (
          <motion.div key="backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 z-40" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
            onClick={() => setSheet("none")} />
        )}
        {sheet === "collab" && (
          <CollabSheet key="collab" username={video.user} onClose={() => setSheet("none")} />
        )}
        {sheet === "creator" && (
          <CreatorSheet key="creator" video={video} onCollab={() => setSheet("collab")} onClose={() => setSheet("none")} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ─── SOUND DETAIL PAGE ────────────────────────────────────────────────────────

function SoundDetail({ sound, onBack }: { sound: Sound; onBack: () => void }) {
  const isDark = useTheme();
  const [saved, setSaved] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [activeVideo, setActiveVideo] = useState<SoundVideo | null>(null);
  const [sheet, setSheet] = useState<"none" | "share">("none");

  const bg = isDark ? "#0c0c0f" : "#f2f5fb";
  const heroOverlay = isDark
    ? "linear-gradient(to bottom,rgba(0,0,0,0.25) 0%,rgba(12,12,15,1) 100%)"
    : "linear-gradient(to bottom,rgba(0,0,0,0.2) 0%,rgba(242,245,251,1) 100%)";
  const statCellBg = isDark ? "#16161a" : "#ffffff";
  const statCellSep = isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)";
  const statValueColor = (c?: string) => c ?? (isDark ? "#fff" : "#0a0e1a");
  const statLabelColor = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const iconBtnBg = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.06)";
  const iconBtnBorder = isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.07)";
  const iconColor = isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.5)";
  const sectionHeadColor = isDark ? "#fff" : "#0a0e1a";
  const sectionCountColor = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const videoRowBg = isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.03)";
  const videoRowBorder = isDark ? "1px solid rgba(255,255,255,0.06)" : "1px solid rgba(0,0,0,0.06)";
  const videoUserColor = isDark ? "#fff" : "#0a0e1a";
  const videoCaptionColor = isDark ? "rgba(255,255,255,0.55)" : "rgba(10,14,26,0.5)";
  const videoMetaColor = isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.4)";
  const chevronColor = isDark ? "rgba(255,255,255,0.2)" : "rgba(10,14,26,0.2)";
  const useSoundBg = isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)";
  const useSoundBorder = isDark ? "1px solid rgba(255,255,255,0.1)" : "1px solid rgba(0,0,0,0.08)";
  const useSoundColor = isDark ? "rgba(255,255,255,0.8)" : "rgba(10,14,26,0.65)";

  return (
    <motion.div
      initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }}
      transition={{ type: "spring", damping: 34, stiffness: 300 }}
      className="absolute inset-0 z-30 overflow-y-auto"
      style={{ background: bg }}
    >
      {/* Hero */}
      <div className="relative">
        <img src={sound.artwork} alt={sound.title} className="w-full object-cover" style={{ height: 260 }} />
        <div className="absolute inset-0" style={{ background: heroOverlay }} />

        <button onClick={onBack} aria-label="Back to sounds" className="absolute top-12 left-4 w-9 h-9 rounded-full flex items-center justify-center"
          style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(8px)" }}>
          <ArrowLeft className="w-4 h-4 text-white" />
        </button>
        <button onClick={() => setSheet("share")} className="absolute top-12 right-4 w-9 h-9 rounded-full flex items-center justify-center"
          style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(8px)" }}>
          <Share2 className="w-4 h-4 text-white" />
        </button>

        <div className="absolute bottom-4 left-4 right-4">
          <p className="text-white/60 text-[11px] font-semibold uppercase tracking-widest mb-1">#{sound.rank} Trending</p>
          <h1 className="text-white font-bold text-2xl leading-tight">{sound.title}</h1>
          <div className="flex items-center gap-2 mt-1">
            <img src={sound.creatorAvatar} alt={sound.creator} className="w-5 h-5 rounded-full object-cover" />
            <span className="text-white/70 text-[13px]">@{sound.creator}</span>
            <span className="text-white/30 text-[13px]">·</span>
            <span className="text-white/50 text-[13px]">{sound.genre}</span>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-px mx-4 mt-1 mb-5 overflow-hidden rounded-2xl" style={{ background: statCellSep }}>
        {[
          { label: "Videos", value: fmtNum(sound.videoCount) },
          { label: "Total Plays", value: fmtNum(sound.totalPlays) },
          { label: "Growth", value: `${sound.growthPct > 0 ? "+" : ""}${sound.growthPct}%`, color: sound.growthPct > 0 ? "#00AEEF" : "#f87171" },
        ].map((s) => (
          <div key={s.label} className="flex flex-col items-center py-4" style={{ background: statCellBg }}>
            <span className="font-bold text-[17px]" style={{ color: statValueColor(s.color) }}>{s.value}</span>
            <span className="text-[11px] mt-0.5" style={{ color: statLabelColor }}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Action row */}
      <div className="flex gap-3 px-4 mb-5">
        <motion.button whileTap={{ scale: 0.96 }} onClick={() => setPlaying((p) => !p)}
          className="flex-1 py-3 rounded-2xl font-bold text-[14px] flex items-center justify-center gap-2 text-white"
          style={{ background: "linear-gradient(135deg,#00AEEF,#0077cc)", boxShadow: "0 6px 20px rgba(0,174,239,0.35)" }}>
          <Play className={`w-4 h-4 ${playing ? "fill-white" : ""}`} />
          {playing ? "Pause Preview" : "Preview Sound"}
        </motion.button>
        <motion.button whileTap={{ scale: 0.96 }} onClick={() => setSaved((s) => !s)}
          className="w-12 h-12 rounded-2xl flex items-center justify-center"
          style={{ background: saved ? "rgba(0,174,239,0.2)" : iconBtnBg, border: saved ? "1px solid rgba(0,174,239,0.5)" : iconBtnBorder }}>
          <Bookmark className="w-5 h-5" style={{ color: saved ? "#00AEEF" : iconColor }} fill={saved ? "#00AEEF" : "none"} />
        </motion.button>
        <motion.button whileTap={{ scale: 0.96 }} onClick={() => setSheet("share")}
          className="w-12 h-12 rounded-2xl flex items-center justify-center"
          style={{ background: iconBtnBg, border: iconBtnBorder }}>
          <Navigation className="w-5 h-5" style={{ color: iconColor }} />
        </motion.button>
      </div>

      {/* Waveform */}
      <AnimatePresence>
        {playing && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 44 }} exit={{ opacity: 0, height: 0 }}
            className="mx-4 mb-5 rounded-xl flex items-center justify-center gap-1 overflow-hidden"
            style={{ background: "rgba(0,174,239,0.08)", border: "1px solid rgba(0,174,239,0.2)" }}>
            {Array.from({ length: 28 }).map((_, i) => (
              <motion.div key={i}
                animate={{ scaleY: [0.3, 1, 0.3] }}
                transition={{ duration: 0.6 + (i % 5) * 0.1, repeat: Infinity, delay: i * 0.04, ease: "easeInOut" }}
                className="rounded-full"
                style={{ width: 3, height: 24, background: "#00AEEF", transformOrigin: "center" }} />
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Videos grid */}
      <div className="px-4 mb-3">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold text-[15px]" style={{ color: sectionHeadColor }}>Videos using this sound</h2>
          <span className="text-[12px]" style={{ color: sectionCountColor }}>{fmtNum(sound.videoCount)}</span>
        </div>

        <div className="space-y-2 mb-4">
          {SOUND_VIDEOS.slice(0, 3).map((v) => (
            <motion.div key={v.id} whileTap={{ scale: 0.98 }}
              onClick={() => setActiveVideo(v)}
              className="flex items-center gap-3 rounded-2xl p-3 cursor-pointer"
              style={{ background: videoRowBg, border: videoRowBorder }}>
              <div className="relative flex-shrink-0 w-14 rounded-xl overflow-hidden" style={{ aspectRatio: "9/16" }}>
                <img src={v.thumb} alt={v.user} className="w-full h-full object-cover" />
                <div className="absolute inset-0 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.3)" }}>
                  <Play className="w-4 h-4 text-white fill-white" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <img src={v.userAvatar} alt={v.user} className="w-5 h-5 rounded-full object-cover" />
                  <span className="font-semibold text-[13px]" style={{ color: videoUserColor }}>@{v.user}</span>
                  {v.collabOpen && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(0,174,239,0.15)", color: "#00AEEF" }}>Collab</span>}
                </div>
                <p className="text-[12px] line-clamp-1" style={{ color: videoCaptionColor }}>{v.caption}</p>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-[11px]" style={{ color: videoMetaColor }}>👁 {fmtNum(v.views)}</span>
                  <span className="text-[11px]" style={{ color: videoMetaColor }}>❤️ {fmtNum(v.likes)}</span>
                  <span className="text-[11px] font-bold" style={{ color: "#00AEEF" }}>⭐ {v.collabScore.toFixed(1)}</span>
                </div>
              </div>
              <ChevronRight className="w-4 h-4 flex-shrink-0" style={{ color: chevronColor }} />
            </motion.div>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-1.5">
          {SOUND_VIDEOS.slice(3).map((v) => (
            <motion.div key={v.id} whileTap={{ scale: 0.96 }}
              onClick={() => setActiveVideo(v)}
              className="relative rounded-xl overflow-hidden cursor-pointer"
              style={{ aspectRatio: "9/16" }}>
              <img src={v.thumb} alt={v.user} className="w-full h-full object-cover" />
              <div className="absolute inset-0" style={{ background: "linear-gradient(to top,rgba(0,0,0,0.7) 0%,transparent 50%)" }} />
              <div className="absolute bottom-1.5 left-1.5 right-1.5">
                <p className="text-white/80 text-[10px] font-semibold truncate">@{v.user}</p>
                <p className="text-white/50 text-[9px]">{fmtNum(v.views)}</p>
              </div>
              <div className="absolute top-1.5 right-1.5">
                <Play className="w-3 h-3 text-white/70 fill-white/70" />
              </div>
            </motion.div>
          ))}
        </div>
      </div>

      {/* Use this sound */}
      <div className="px-4 pb-12">
        <motion.button whileTap={{ scale: 0.97 }}
          className="w-full py-4 rounded-2xl font-bold text-[15px] flex items-center justify-center gap-2 mt-4"
          style={{ background: useSoundBg, border: useSoundBorder, color: useSoundColor }}>
          <Mic2 className="w-5 h-5" /> Use This Sound
        </motion.button>
      </div>

      {/* Full-screen video viewer */}
      <AnimatePresence>
        {activeVideo && (
          <VideoViewer key={activeVideo.id} video={activeVideo} sound={sound} onClose={() => setActiveVideo(null)} />
        )}
      </AnimatePresence>

      {/* Share sheet */}
      <AnimatePresence>
        {sheet === "share" && (
          <>
            <motion.div key="sb" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="absolute inset-0 z-40" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
              onClick={() => setSheet("none")} />
            <SoundShareSheet key="ss" title={sound.title} onClose={() => setSheet("none")} />
          </>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ─── TRENDING SOUNDS PAGE ─────────────────────────────────────────────────────

export function TrendingSounds({
  onBack, initialSoundId = null, onOpenPaid,
}: {
  onBack: () => void;
  /** Opens straight onto a sound's detail — how a search result lands here. */
  initialSoundId?: string | null;
  onOpenPaid?: () => void;
}) {
  const isDark = useTheme();
  const [activeGenre, setActiveGenre] = useState("All");
  const [savedSounds, setSavedSounds] = useState<Record<string, boolean>>({});
  const [remoteSounds, setRemoteSounds] = useState<Sound[] | null>(null);
  useEffect(() => {
    let active = true;
    fetchTrendingSounds().then((sounds) => {
      if (active && sounds?.length) setRemoteSounds(sounds);
    });
    return () => { active = false; };
  }, []);
  const sounds = remoteSounds ?? SOUNDS;
  const [selectedSound, setSelectedSound] = useState<Sound | null>(
    () => sounds.find((s) => s.id === initialSoundId) ?? null,
  );
  useEffect(() => {
    if (initialSoundId && !selectedSound) {
      setSelectedSound(sounds.find((s) => s.id === initialSoundId) ?? null);
    }
  }, [initialSoundId, selectedSound, sounds]);

  const filtered = activeGenre === "All" ? sounds : sounds.filter((s) => s.genre === activeGenre);

  const toggleSave = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSavedSounds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const bg = isDark ? "#0c0c0f" : "#f2f5fb";
  const headerBorder = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.07)";
  const backBtnBg = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)";
  const backIconColor = isDark ? "#fff" : "#0a0e1a";
  const headingColor = isDark ? "#fff" : "#0a0e1a";
  const subheadColor = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const genreInactiveBg = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)";
  const genreInactiveColor = isDark ? "rgba(255,255,255,0.5)" : "rgba(10,14,26,0.45)";
  const rowBg = isDark ? "rgba(255,255,255,0.04)" : "rgba(255,255,255,0.7)";
  const rowBorder = isDark ? "1px solid rgba(255,255,255,0.06)" : "1px solid rgba(0,0,0,0.06)";
  const titleColor = isDark ? "#fff" : "#0a0e1a";
  const metaColor = isDark ? "rgba(255,255,255,0.45)" : "rgba(10,14,26,0.45)";
  const metaColor2 = isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.35)";
  const rankInactiveColor = isDark ? "rgba(255,255,255,0.3)" : "rgba(10,14,26,0.25)";
  const savedInactiveBg = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)";
  const savedInactiveIcon = isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.35)";
  const chevronColor = isDark ? "rgba(255,255,255,0.2)" : "rgba(10,14,26,0.2)";

  return (
    <div className="absolute inset-0 z-20 overflow-hidden" style={{ background: bg }}>
      {/* Header */}
      <div className="flex items-center gap-4 px-4 pt-14 pb-4" style={{ borderBottom: `1px solid ${headerBorder}` }}>
        <button onClick={onBack} aria-label="Back" className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: backBtnBg }}>
          <ArrowLeft className="w-4 h-4" style={{ color: backIconColor }} />
        </button>
        <div className="flex-1">
          <h1 className="font-bold text-xl" style={{ color: headingColor }}>Trending Sounds</h1>
          <p className="text-[12px]" style={{ color: subheadColor }}>Updated hourly</p>
        </div>
        {onOpenPaid && <PaidDiscoveryLink onOpen={onOpenPaid} />}
        <div className="px-3 py-1.5 rounded-full text-[11px] font-bold" style={{ background: "rgba(0,174,239,0.15)", color: "#00AEEF", border: "1px solid rgba(0,174,239,0.3)" }}>
          🔴 LIVE
        </div>
      </div>

      {/* Genre filter */}
      <div className="overflow-x-auto" style={{ scrollbarWidth: "none" }}>
        <div className="flex gap-2 px-4 py-3" style={{ minWidth: "max-content" }}>
          {GENRES.map((g) => (
            <button key={g} onClick={() => setActiveGenre(g)}
              className="px-4 py-1.5 rounded-full text-[12px] font-semibold flex-shrink-0 transition-all"
              style={{ background: activeGenre === g ? "#00AEEF" : genreInactiveBg, color: activeGenre === g ? "#fff" : genreInactiveColor, boxShadow: activeGenre === g ? "0 4px 14px rgba(0,174,239,0.35)" : "none" }}>
              {g}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="overflow-y-auto" style={{ height: "calc(100% - 160px)" }}>
        <div className="px-4 pb-10 space-y-2">
          <AnimatePresence>
            {filtered.map((sound, i) => {
              const isSaved = !!savedSounds[sound.id];
              const trending = sound.growthPct > 0;
              return (
                <motion.div key={sound.id}
                  initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }}
                  transition={{ delay: i * 0.04 }}
                  onClick={() => setSelectedSound(sound)}
                  className="flex items-center gap-3 rounded-2xl p-3 cursor-pointer active:opacity-80 transition-opacity"
                  style={{ background: rowBg, border: rowBorder }}>
                  <div className="w-6 flex-shrink-0 text-center">
                    <span className="font-bold text-[13px]" style={{ color: sound.rank <= 3 ? "#00AEEF" : rankInactiveColor }}>{sound.rank}</span>
                  </div>
                  <div className="relative flex-shrink-0">
                    <img src={sound.artwork} alt={sound.title} className="w-14 h-14 rounded-xl object-cover" />
                    {sound.rank <= 3 && (
                      <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold"
                        style={{ background: "#00AEEF", boxShadow: "0 2px 8px rgba(0,174,239,0.5)" }}>🔥</div>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-semibold text-[14px] truncate" style={{ color: titleColor }}>{sound.title}</p>
                    <p className="text-[12px] truncate" style={{ color: metaColor }}>@{sound.creator} · {sound.genre}</p>
                    <div className="flex items-center gap-3 mt-1.5">
                      <span className="text-[11px] flex items-center gap-1" style={{ color: metaColor2 }}><Play className="w-2.5 h-2.5" />{fmtNum(sound.videoCount)} videos</span>
                      <span className="text-[11px]" style={{ color: metaColor2 }}>·</span>
                      <span className="text-[11px]" style={{ color: metaColor2 }}>{fmtNum(sound.totalPlays)} plays</span>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-2 flex-shrink-0">
                    <div className="flex items-center gap-1 px-2 py-0.5 rounded-full"
                      style={{ background: trending ? "rgba(0,174,239,0.12)" : "rgba(248,113,113,0.12)", border: `1px solid ${trending ? "rgba(0,174,239,0.3)" : "rgba(248,113,113,0.3)"}` }}>
                      {trending ? <TrendingUp className="w-3 h-3" style={{ color: "#00AEEF" }} /> : <TrendingDown className="w-3 h-3 text-red-400" />}
                      <span className="text-[10px] font-bold" style={{ color: trending ? "#00AEEF" : "#f87171" }}>{trending ? "+" : ""}{sound.growthPct}%</span>
                    </div>
                    <button onClick={(e) => toggleSave(sound.id, e)}
                      className="w-7 h-7 rounded-full flex items-center justify-center"
                      style={{ background: isSaved ? "rgba(0,174,239,0.15)" : savedInactiveBg }}>
                      <Bookmark className="w-3.5 h-3.5" style={{ color: isSaved ? "#00AEEF" : savedInactiveIcon }} fill={isSaved ? "#00AEEF" : "none"} />
                    </button>
                  </div>
                  <ChevronRight className="w-4 h-4 flex-shrink-0" style={{ color: chevronColor }} />
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </div>

      {/* Sound detail */}
      <AnimatePresence>
        {selectedSound && (
          <SoundDetail key={selectedSound.id} sound={selectedSound} onBack={() => setSelectedSound(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}
