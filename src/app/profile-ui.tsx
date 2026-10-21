// ─── PROFILE UI KIT ──────────────────────────────────────────────────────────
//
// The identity and interaction primitives, in one place so no screen reimplements
// them. Two rules shaped this file:
//
//   • Every avatar in the app is an `<Avatar>`. It resolves the image, falls back
//     to the initial when there is none *or when the image fails to load*, and
//     is the only place that decision is made. That is what makes an uploaded
//     photo show up identically on a profile, in the feed, under a comment and
//     in a message thread.
//
//   • Every follow control is a `<FollowButton>`. The follow state machine —
//     optimistic flip, pending, rollback on failure — lives in `follow-store.ts`
//     and is consumed here once, so the feed rail and the profile header cannot
//     drift apart or disagree about who is followed.
//
// Colours come from the settings token set rather than a second palette: these
// components render on both the light and dark surfaces that Settings defined.

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  BarChart3, Check, Loader2, MessageCircle, Pencil, Plus, Settings, Share2, Sparkles, Star, UserMinus,
} from "lucide-react";
import { useTheme } from "./ThemeContext";
import { ACCENT, useTokens } from "./settings-ui";
import { type Creator } from "./creators";
import { useFollow, useFollowerCount } from "./follow-store";
import { useViewer } from "./session";
import { useMediaSource } from "./post-media";

// ─── FORMATTING ──────────────────────────────────────────────────────────────

/** 4820 → "4.8K", 1284000 → "1.3M". Shared so every count reads the same. */
export const formatCount = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M`
  : n >= 10_000 ? `${Math.round(n / 1_000)}K`
  : n >= 1_000 ? `${(n / 1_000).toFixed(1)}K`
  : String(n);

// ─── AVATAR ──────────────────────────────────────────────────────────────────

/** First character of the display name, or "?" — emoji-safe. */
const initialOf = (name: string) => {
  const trimmed = name.trim().replace(/^@/, "");
  return trimmed ? [...trimmed][0].toUpperCase() : "?";
};

export function Avatar({
  src, name, color = ACCENT, size = 40, ring = false, ringColor = ACCENT,
  online = false, badge, onClick, className = "",
}: {
  /** "" or undefined renders the fallback rather than a broken image. */
  src?: string;
  /** Display name or handle — the fallback initial and the alt text. */
  name: string;
  /** Fill behind the fallback initial. */
  color?: string;
  size?: number;
  ring?: boolean;
  ringColor?: string;
  online?: boolean;
  /** Corner ornament, e.g. the collab "C" in the inbox. */
  badge?: ReactNode;
  /** When set, the avatar becomes a button — used to open the profile. */
  onClick?: () => void;
  className?: string;
}) {
  const isDark = useTheme();
  // Keyed by URL so swapping in a new photo re-tries loading rather than
  // inheriting the previous one's failure.
  const [failed, setFailed] = useState<string | null>(null);
  const showImage = !!src && failed !== src;

  const inner = (
    <>
      {showImage ? (
        <img
          src={src}
          alt={name}
          onError={() => setFailed(src!)}
          className="w-full h-full object-cover"
          style={{ borderRadius: "inherit" }}
        />
      ) : (
        <span
          aria-hidden
          className="w-full h-full flex items-center justify-center font-bold text-white select-none"
          style={{ background: color, fontSize: Math.max(10, Math.round(size * 0.42)), borderRadius: "inherit" }}
        >
          {initialOf(name)}
        </span>
      )}
    </>
  );

  const frame = (
    <span
      className={`relative inline-block flex-shrink-0 overflow-hidden rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        border: ring ? `${size >= 72 ? 3 : 2}px solid ${ringColor}` : undefined,
        background: color,
      }}
    >
      {inner}
    </span>
  );

  const dotSize = Math.max(8, Math.round(size * 0.24));

  const content = (
    <span className="relative inline-flex flex-shrink-0" style={{ lineHeight: 0 }}>
      {frame}
      {online && (
        <span
          className="absolute rounded-full"
          style={{
            width: dotSize, height: dotSize, right: 0, bottom: 0,
            background: "#4ade80",
            border: `2px solid ${isDark ? "#000" : "#f2f5fb"}`,
          }}
        />
      )}
      {badge && <span className="absolute -top-1 -right-1">{badge}</span>}
    </span>
  );

  if (!onClick) return content;

  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.92 }}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      aria-label={`View @${name.replace(/^@/, "")}'s profile`}
      className="inline-flex flex-shrink-0"
      style={{ lineHeight: 0 }}
    >
      {content}
    </motion.button>
  );
}

/**
 * The signed-in creator's avatar. Reads the session directly, so a composer or
 * header can never render a stale copy of the viewer's photo — which is the
 * whole point of it being one component.
 */
export function ViewerAvatar({ size = 32, ring, onClick }: { size?: number; ring?: boolean; onClick?: () => void }) {
  const viewer = useViewer();
  return (
    <Avatar src={viewer.avatarUrl} name={viewer.displayName || viewer.username}
      color={viewer.avatarColor} size={size} ring={ring} onClick={onClick} />
  );
}

// ─── IDENTITY BITS ───────────────────────────────────────────────────────────

export function VerifiedBadge({ size = 14 }: { size?: number }) {
  return (
    <span
      title="Verified creator"
      aria-label="Verified creator"
      className="inline-flex items-center justify-center rounded-full flex-shrink-0"
      style={{ width: size, height: size, background: ACCENT }}
    >
      <Check style={{ width: size * 0.62, height: size * 0.62 }} className="text-white" strokeWidth={4} />
    </span>
  );
}

/** The collab score, the number this whole product is organised around. */
export function CollabScorePill({
  score, count, compact = false, onMedia = false,
}: {
  score: number;
  count?: number;
  compact?: boolean;
  /** Sitting on video or imagery: the label stays light whatever the theme is. */
  onMedia?: boolean;
}) {
  const isDark = useTheme() || onMedia;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full ${compact ? "px-2 py-0.5" : "px-3 py-1.5"}`}
      style={{
        background: "rgba(0,174,239,0.12)",
        border: "1px solid rgba(0,174,239,0.3)",
        backdropFilter: "blur(8px)",
      }}
    >
      <Star className={compact ? "w-3 h-3" : "w-3.5 h-3.5"} style={{ color: ACCENT, fill: ACCENT }} />
      <span className={`font-bold ${compact ? "text-[11px]" : "text-[13px]"}`} style={{ color: ACCENT }}>
        {score.toFixed(1)}
      </span>
      <span className={compact ? "text-[10px]" : "text-[12px]"}
        style={{ color: isDark ? "rgba(255,255,255,0.45)" : "rgba(10,14,26,0.45)" }}>
        Collab Score{count !== undefined ? ` · ${formatCount(count)} collabs` : ""}
      </span>
    </span>
  );
}

/** Follower / following / collab counts. Tappable entries open a connections list. */
export function StatCounts({
  items,
}: { items: { label: string; value: number; onClick?: () => void }[] }) {
  const isDark = useTheme();
  const t = useTokens(isDark);
  return (
    <div className="flex items-stretch">
      {items.map((item, i) => {
        const body = (
          <>
            <span className="font-extrabold text-[17px] leading-none" style={{ color: t.heading }}>
              {formatCount(item.value)}
            </span>
            <span className="text-[12px] mt-1" style={{ color: t.sub }}>{item.label}</span>
          </>
        );
        return (
          <div key={item.label} className="flex items-stretch">
            {i > 0 && <div className="w-px my-1 mx-4" style={{ background: t.divider }} />}
            {item.onClick ? (
              <motion.button whileTap={{ scale: 0.95 }} onClick={item.onClick}
                className="flex flex-col items-center px-1 transition-opacity active:opacity-70">
                {body}
              </motion.button>
            ) : (
              <div className="flex flex-col items-center px-1">{body}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── ACTION BUTTONS ──────────────────────────────────────────────────────────
//
// One button, four looks. Every profile action goes through it so a Follow, an
// Edit Profile and a Message are the same height, radius and press response
// wherever they are used.

export type ActionVariant = "primary" | "secondary" | "ghost" | "icon";

export function ActionButton({
  children, onClick, variant = "secondary", icon, loading = false, disabled = false,
  grow = false, ariaLabel, ariaPressed, title,
}: {
  children?: ReactNode;
  onClick?: () => void;
  variant?: ActionVariant;
  icon?: ReactNode;
  loading?: boolean;
  disabled?: boolean;
  /** Fill the available width — how the profile action bar splits its row. */
  grow?: boolean;
  ariaLabel?: string;
  ariaPressed?: boolean;
  title?: string;
}) {
  const isDark = useTheme();
  const t = useTokens(isDark);
  const off = disabled || loading;

  const skin: Record<ActionVariant, { background: string; color: string; border: string; shadow?: string }> = {
    primary: {
      background: `linear-gradient(135deg,${ACCENT},#0077cc)`,
      color: "#fff",
      border: "1px solid transparent",
      shadow: "0 6px 20px rgba(0,174,239,0.35)",
    },
    secondary: {
      background: isDark ? "rgba(255,255,255,0.10)" : "rgba(10,14,26,0.06)",
      color: t.heading,
      border: isDark ? "1px solid rgba(255,255,255,0.18)" : "1px solid rgba(10,14,26,0.12)",
    },
    ghost: {
      background: "transparent",
      color: t.sub,
      border: isDark ? "1px solid rgba(255,255,255,0.14)" : "1px solid rgba(10,14,26,0.1)",
    },
    icon: {
      background: isDark ? "rgba(255,255,255,0.10)" : "rgba(10,14,26,0.06)",
      color: t.heading,
      border: isDark ? "1px solid rgba(255,255,255,0.16)" : "1px solid rgba(10,14,26,0.1)",
    },
  };

  const s = skin[variant];
  const isIcon = variant === "icon";

  return (
    <motion.button
      type="button"
      whileTap={off ? {} : { scale: 0.96 }}
      onClick={onClick}
      disabled={off}
      aria-label={ariaLabel}
      aria-pressed={ariaPressed}
      title={title}
      className={`${isIcon ? "w-10 h-10 rounded-full" : "h-11 px-4 rounded-full"} ${grow ? "flex-1" : ""} flex items-center justify-center gap-2 font-bold text-[14px] flex-shrink-0`}
      style={{
        background: s.background,
        color: s.color,
        border: s.border,
        boxShadow: s.shadow,
        opacity: off ? 0.55 : 1,
        backdropFilter: variant === "secondary" || isIcon ? "blur(8px)" : undefined,
      }}
    >
      {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : icon}
      {!isIcon && children}
    </motion.button>
  );
}

// ─── FOLLOW BUTTON ───────────────────────────────────────────────────────────

/**
 * The one follow control. Not following reads as a filled brand pill; following
 * flips to neutral glass with a check, and hovering it offers "Unfollow" so the
 * second tap is never ambiguous. State comes from the store, so pressing this on
 * a profile also updates the feed rail badge and the Following count.
 */
export function FollowButton({
  creatorId, username, grow = false, size = "md",
}: {
  creatorId: string;
  /** Only used for the accessible label. */
  username: string;
  grow?: boolean;
  size?: "sm" | "md";
}) {
  const { following, pending, error, toggle } = useFollow(creatorId);
  const [hovered, setHovered] = useState(false);
  const isDark = useTheme();

  // "Unfollow" on hover is a pointer affordance; on touch it would flash on tap
  // and read as the button having changed meaning, so it stays hover-only.
  const label = pending ? (following ? "Following" : "Follow")
    : following ? (hovered ? "Unfollow" : "Following")
    : "Follow";

  const small = size === "sm";

  return (
    <div className={`relative ${grow ? "flex-1" : ""}`}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <motion.button
        type="button"
        whileTap={pending ? {} : { scale: 0.96 }}
        onClick={(e) => { e.stopPropagation(); toggle(); }}
        disabled={pending}
        aria-pressed={following}
        aria-label={following ? `Unfollow @${username}` : `Follow @${username}`}
        className={`w-full ${small ? "h-9 px-3.5 text-[13px]" : "h-11 px-5 text-[14px]"} rounded-full flex items-center justify-center gap-1.5 font-bold`}
        style={{
          background: following
            ? (isDark ? "rgba(255,255,255,0.10)" : "rgba(10,14,26,0.06)")
            : `linear-gradient(135deg,${ACCENT},#0077cc)`,
          color: following ? (isDark ? "#fff" : "#0a0e1a") : "#fff",
          border: following
            ? (isDark ? "1px solid rgba(255,255,255,0.22)" : "1px solid rgba(10,14,26,0.14)")
            : "1px solid transparent",
          boxShadow: following ? "none" : "0 6px 20px rgba(0,174,239,0.35)",
          backdropFilter: following ? "blur(8px)" : undefined,
          transition: "background 0.2s, box-shadow 0.2s, border-color 0.2s",
        }}
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={`${pending ? "p" : ""}${label}`}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.14 }}
            className="flex items-center gap-1.5"
          >
            {pending
              ? <Loader2 className={small ? "w-3.5 h-3.5 animate-spin" : "w-4 h-4 animate-spin"} />
              : following
                ? (hovered
                    ? <UserMinus className={small ? "w-3.5 h-3.5" : "w-4 h-4"} />
                    : <Check className={small ? "w-3.5 h-3.5" : "w-4 h-4"} strokeWidth={3} />)
                : <Plus className={small ? "w-3.5 h-3.5" : "w-4 h-4"} strokeWidth={3} />}
            {label}
          </motion.span>
        </AnimatePresence>
      </motion.button>

      {/* A failed write rolls the graph back, so say why rather than letting the
          button appear to have ignored the tap. */}
      <AnimatePresence>
        {error && (
          <motion.p
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="absolute left-0 right-0 top-full mt-1 text-[11px] text-center leading-snug"
            style={{ color: "#f87171" }}
            role="status"
          >
            {error}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── PROFILE ACTION BAR ──────────────────────────────────────────────────────

/**
 * Which actions a profile offers, decided in one place. The owner gets Edit
 * Profile, Share and Settings; everyone else gets Follow, Message and — only
 * when that creator is actually open to it — Collab. Screens pass handlers, not
 * rules, so a new surface (search result, collab request card) gets the same
 * behaviour by rendering this.
 */
export function ProfileActionBar({
  creator, isOwner, onEdit, onShare, onSettings, onDashboard, onMessage, onCollab,
}: {
  creator: Pick<Creator, "id" | "username" | "openToCollab">;
  isOwner: boolean;
  onEdit?: () => void;
  onShare?: () => void;
  onSettings?: () => void;
  /** Owner only — the creator dashboard behind this profile's numbers. */
  onDashboard?: () => void;
  onMessage?: () => void;
  onCollab?: () => void;
}) {
  const isDark = useTheme();
  const t = useTokens(isDark);

  if (isOwner) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ActionButton variant="primary" grow icon={<Pencil className="w-4 h-4" />} onClick={onEdit}>
            Edit Profile
          </ActionButton>
          {onShare && (
            <ActionButton variant="icon" ariaLabel="Share profile" title="Share profile"
              icon={<Share2 className="w-4 h-4" />} onClick={onShare} />
          )}
          {onSettings && (
            <ActionButton variant="icon" ariaLabel="Settings" title="Settings"
              icon={<Settings className="w-4 h-4" />} onClick={onSettings} />
          )}
        </div>
        {onDashboard && (
          <ActionButton variant="secondary" grow icon={<BarChart3 className="w-4 h-4" />} onClick={onDashboard}>
            Creator Dashboard
          </ActionButton>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <FollowButton creatorId={creator.id} username={creator.username} grow />
        {onMessage && (
          <ActionButton variant="secondary" grow icon={<MessageCircle className="w-4 h-4" />} onClick={onMessage}>
            Message
          </ActionButton>
        )}
        {onShare && (
          <ActionButton variant="icon" ariaLabel="Share profile" title="Share profile"
            icon={<Share2 className="w-4 h-4" />} onClick={onShare} />
        )}
      </div>
      {onCollab && (
        creator.openToCollab ? (
          <ActionButton variant="primary" grow icon={<Sparkles className="w-4 h-4" />} onClick={onCollab}>
            Request Collab
          </ActionButton>
        ) : (
          // Not a disabled button: a dead control invites repeated tapping. Say
          // what the state is instead.
          <div className="h-11 rounded-full flex items-center justify-center text-[13px] font-semibold"
            style={{ background: t.chipBg, border: t.chipBorder, color: t.sub }}>
            Not taking collabs right now
          </div>
        )
      )}
    </div>
  );
}

// ─── SEGMENTED TABS ──────────────────────────────────────────────────────────

export function SegmentedTabs<T extends string>({
  tabs, active, onChange, layoutId,
}: {
  tabs: { id: T; label: string; count?: number }[];
  active: T;
  onChange: (id: T) => void;
  /** Unique per instance, so two tab rows never share the underline animation. */
  layoutId: string;
}) {
  const isDark = useTheme();
  const t = useTokens(isDark);
  return (
    <div className="flex" style={{ borderBottom: `1px solid ${t.divider}` }} role="tablist">
      {tabs.map((tab) => {
        const on = tab.id === active;
        return (
          <button key={tab.id} role="tab" aria-selected={on} onClick={() => onChange(tab.id)}
            className="flex-1 relative pb-3 pt-2 flex items-center justify-center gap-1.5">
            <span className="text-[13px]" style={{ color: on ? ACCENT : t.sub, fontWeight: on ? 700 : 600 }}>
              {tab.label}
            </span>
            {tab.count !== undefined && (
              <span className="text-[11px] font-semibold" style={{ color: on ? ACCENT : t.chevron }}>
                {formatCount(tab.count)}
              </span>
            )}
            {on && (
              <motion.div layoutId={layoutId} className="absolute bottom-0 left-1/4 right-1/4 h-0.5 rounded-full"
                style={{ background: ACCENT }} />
            )}
          </button>
        );
      })}
    </div>
  );
}

// ─── CREATOR ROW ─────────────────────────────────────────────────────────────

/** A creator in a list — connections sheets, and any future search results. */
export function CreatorRow({
  creator, onOpen, showFollow = true, last = false,
}: {
  creator: Creator;
  onOpen?: (username: string) => void;
  showFollow?: boolean;
  last?: boolean;
}) {
  const isDark = useTheme();
  const t = useTokens(isDark);
  const followers = useFollowerCount(creator);

  return (
    <div className="flex items-center gap-3 px-5 py-3"
      style={{ borderBottom: last ? "none" : `1px solid ${t.divider}` }}>
      <Avatar src={creator.avatarUrl} name={creator.displayName} color={creator.avatarColor}
        size={44} online={creator.online} onClick={onOpen ? () => onOpen(creator.username) : undefined} />
      <button onClick={() => onOpen?.(creator.username)} className="flex-1 min-w-0 text-left">
        <span className="flex items-center gap-1.5">
          <span className="font-bold text-[14px] truncate" style={{ color: t.heading }}>@{creator.username}</span>
          {creator.verified && <VerifiedBadge size={13} />}
        </span>
        <span className="block text-[12px] truncate" style={{ color: t.sub }}>
          {creator.displayName} · {formatCount(followers)} followers
        </span>
      </button>
      {showFollow && <FollowButton creatorId={creator.id} username={creator.username} size="sm" />}
    </div>
  );
}

// ─── THUMBNAIL ───────────────────────────────────────────────────────────────

/**
 * Post and playlist artwork. Same principle as `<Avatar>`: a URL that fails to
 * load falls back to a tinted placeholder rather than a broken-image icon.
 */
export function Thumb({
  src, alt = "", className = "", style,
}: { src: string; alt?: string; className?: string; style?: CSSProperties }) {
  const media = useMediaSource(src);

  if (!src || !media.url) {
    return (
      <span className={className} role={src && media.error ? "alert" : undefined}
        style={{ ...style, background: "linear-gradient(135deg, rgba(0,174,239,0.22), rgba(124,58,237,0.18))" }}>
        {src && media.error ? "Media unavailable" : null}
      </span>
    );
  }
  return (
    <img src={media.url} alt={alt} loading="lazy" onError={media.onError}
      className={className} style={style} />
  );
}

// ─── CONTENT GRID ────────────────────────────────────────────────────────────

/**
 * The 3-up grid of posts on a profile. A tile is only a button when it has
 * somewhere to go — `canOpen` decides that — so the grid never offers a tap that
 * does nothing.
 */
export function ContentGrid({
  items, onOpen, canOpen,
}: {
  items: { id: string; thumbnail: string; views: number; collabWith?: string }[];
  onOpen?: (id: string) => void;
  canOpen?: (id: string) => boolean;
}) {
  return (
    <div className="grid grid-cols-3 gap-1">
      {items.map((item) => {
        const openable = !!onOpen && (!canOpen || canOpen(item.id));
        const overlay = (
          <>
            {item.thumbnail
              ? <Thumb src={item.thumbnail} className="absolute inset-0 w-full h-full object-cover" />
              : <span role="alert" className="absolute inset-0 flex items-center justify-center text-xs">Media unavailable</span>}
            <span className="absolute inset-x-0 bottom-0 h-14"
              style={{ background: "linear-gradient(to top, rgba(0,0,0,0.75), transparent)" }} />
            <span className="absolute bottom-1.5 left-2 text-white text-[11px] font-bold drop-shadow">
              ▶ {formatCount(item.views)}
            </span>
            {item.collabWith && (
              <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded-full text-[9px] font-bold"
                style={{ background: "rgba(0,174,239,0.85)", color: "#fff" }}>
                COLLAB
              </span>
            )}
          </>
        );
        const frame = { aspectRatio: "9 / 14", background: "rgba(0,0,0,0.2)" };

        return openable ? (
          <motion.button key={item.id} whileTap={{ scale: 0.97 }} onClick={() => onOpen!(item.id)}
            className="relative overflow-hidden" style={frame}>
            {overlay}
          </motion.button>
        ) : (
          <div key={item.id} className="relative overflow-hidden" style={frame}>{overlay}</div>
        );
      })}
    </div>
  );
}

// ─── SCROLL-AWARE HEADER HELPER ──────────────────────────────────────────────

/**
 * Tracks a scroll container so a profile header can compact as it scrolls. Kept
 * here because both the owner and visitor profiles use it.
 */
export function useScrolled(threshold = 72) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => setScrolled(el.scrollTop > threshold);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [threshold]);

  return { ref, scrolled };
}
