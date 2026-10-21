// ─── CREATOR DIRECTORY ───────────────────────────────────────────────────────
//
// One source of truth for *who* a creator is. Before this file, the same person
// was re-typed in five places — the feed, the comment seeds, the inbox, the
// trending grid — each with its own copy of their avatar URL and collab score,
// so a profile could disagree with the feed it was opened from.
//
// Everything that renders an identity (avatar, handle, collab score, follower
// count) now resolves it from here by id or handle. Follow *state* is not part
// of a creator: it belongs to the viewer, so it lives in `follow-store.ts`.
//
// ⚠️  PROTOTYPE DATA — this is a seeded directory, not a fetched one.
//
// ── Replacing this with a real backend ──────────────────────────────────────
// Keep the lookup signatures and no screen changes:
//
//   creatorById() / creatorByUsername() → GET /creators/:idOrHandle
//   FEED                                → GET /feed
//   creator.posts / creator.playlists   → GET /creators/:id/posts | /playlists
//   OWN_STATS                           → GET /me/stats
//
// `followers` is the count *excluding* the signed-in viewer, so the viewer's own
// follow can be layered on optimistically without waiting for a refetch — see
// `useFollowerCount` in `follow-store.ts`.

// ─── SHAPES ──────────────────────────────────────────────────────────────────

/** A grid tile on a profile — one post the creator published. */
export interface ContentItem {
  id: string;
  thumbnail: string;
  mediaType?: string;
  caption: string;
  views: number;
  likes: number;
  status?: "draft" | "published" | "scheduled";
  scheduledAt?: string;
  /** Handle of the co-creator, when the post came out of a collab. */
  collabWith?: string;
}

/** A collection tile — playlists and series both render as one. */
export interface Playlist {
  id: string;
  title: string;
  cover: string;
  /** "12 tracks", "6 episodes" — the noun varies by creator, so it is stored. */
  itemLabel: string;
  plays: number;
}

/** A post that is also in the feed, so it carries the feed-only counters. */
export interface FeedVideo extends ContentItem {
  creatorId: string;
  hashtags: string[];
  audio: string;
  comments: number;
  shares: number;
  saves: number;
  /** Durable media reference, for either an image or a video. */
  mediaUrl?: string;
  durationSec?: number;
  /** Whether the signed-in viewer has already liked this post. */
  isLiked?: boolean;
  /** Whether the signed-in viewer has already saved this post. */
  isSaved?: boolean;
  /** Whether the signed-in viewer has already shared this post. */
  isShared?: boolean;
}

export interface Creator {
  id: string;
  /** Handle, without the @. Unique — it is how creators address each other. */
  username: string;
  displayName: string;
  /** May be "": every avatar renders through `<Avatar>`, which falls back. */
  avatarUrl: string;
  /** Fill behind the initial when no image is set or the image fails to load. */
  avatarColor: string;
  bio: string;
  location: string;
  website: string;
  verified: boolean;
  online: boolean;
  /** Free-text status shown as a chip, e.g. "Available for Collaboration". */
  collabStatus: string;
  collabScore: number;
  collabCount: number;
  /** Followers *excluding* the signed-in viewer — see the note at the top. */
  followers: number;
  following: number;
  openToCollab: boolean;
  /** Matches `RESPONSE_TIME_OPTIONS` in `settings-store.ts`. */
  responseTime: string;
  posts: ContentItem[];
  playlists: Playlist[];
}

// ─── IMAGE POOL ──────────────────────────────────────────────────────────────
//
// Portraits and post stills are pooled so a creator's grid can be seeded
// without another wall of URLs per person.

const PORTRAIT = (id: string) => `https://images.unsplash.com/photo-${id}?w=256&h=256&fit=crop&auto=format`;
const STILL = (id: string) => `https://images.unsplash.com/photo-${id}?w=400&h=711&fit=crop&auto=format`;
const COVER = (id: string) => `https://images.unsplash.com/photo-${id}?w=400&h=400&fit=crop&auto=format`;

const STILLS = [
  "1598488035139-bdbb2231ce04", "1493225457124-a3eb161ffa5f", "1516280440614-37939bbacd81",
  "1611532736597-de2d4265fba3", "1485846234645-a62644f84728", "1514525253161-7a46d19cd819",
  "1470225620780-dba8ba36b745", "1459749411175-04bf5292ceea", "1504704911898-68304a7d2807",
  "1492684223066-81342ee5ff30", "1499415479124-43c32433a620", "1524650359799-842906ca1c06",
];

/**
 * Deterministic tiles for a creator, so a grid never reshuffles on re-render.
 * The stride is coprime with the pool size, which is what stops a grid from
 * showing the same still twice a few tiles apart.
 */
function stills(seed: number, count: number): string[] {
  return Array.from({ length: count }, (_, i) => STILL(STILLS[(seed * 7 + i * 5) % STILLS.length]));
}

// ─── FEED ────────────────────────────────────────────────────────────────────
//
// The feed's five videos. Each one is also the first tile on its creator's
// profile grid (see `postsFor`), so opening a profile from the feed shows the
// post you came from rather than an unrelated set.

export const FEED: FeedVideo[] = [
  {
    id: "1", creatorId: "1",
    caption: "Late night studio sessions always hit different 🎵 new track dropping this Friday",
    hashtags: ["#producer", "#musicmaker", "#newmusic"],
    audio: "Original Sound — zara.creates",
    views: 1_284_000, likes: 284_700, comments: 4_820, shares: 12_400, saves: 9_300,
    thumbnail: STILL("1598488035139-bdbb2231ce04"),
  },
  {
    id: "2", creatorId: "2",
    caption: "Golden hour was NOT messing around today 📸 caught the whole shift in one frame",
    hashtags: ["#photography", "#goldenhour", "#creator"],
    audio: "golden hour — JVKE",
    views: 2_140_000, likes: 531_200, comments: 7_650, shares: 23_800, saves: 18_900,
    thumbnail: STILL("1493225457124-a3eb161ffa5f"),
  },
  {
    id: "3", creatorId: "3",
    caption: "The drop at 2:14 will literally change your life. You've been warned 🔊",
    hashtags: ["#dj", "#electronicmusic", "#setlife"],
    audio: "HYPERSONIC — nova.dj",
    views: 4_020_000, likes: 892_400, comments: 11_200, shares: 45_600, saves: 32_100,
    thumbnail: STILL("1516280440614-37939bbacd81"),
  },
  {
    id: "4", creatorId: "4",
    caption: "Built this entire app in a weekend. No sleep, just vibes and caffeine ⚡",
    hashtags: ["#buildinpublic", "#devtok", "#indiedev"],
    audio: "lo-fi beats — study playlist",
    views: 604_000, likes: 127_600, comments: 3_450, shares: 8_900, saves: 15_700,
    thumbnail: STILL("1611532736597-de2d4265fba3"),
  },
  {
    id: "5", creatorId: "5",
    caption: "Shot this on a $200 camera and people think it's RED footage 🎬 cinematography is 90% light",
    hashtags: ["#filmmaking", "#cinematography", "#indiefilm"],
    audio: "Cinematic Score — Artlist",
    views: 1_620_000, likes: 344_900, comments: 6_780, shares: 19_200, saves: 24_600,
    thumbnail: STILL("1485846234645-a62644f84728"),
  },
];

/** The feed post first, then seeded back-catalogue tiles. */
function postsFor(creatorId: string, seed: number, extra: number, collabWith?: string): ContentItem[] {
  const feed = FEED.find((f) => f.creatorId === creatorId);
  const back = stills(seed, extra + 1)
    .filter((thumbnail) => thumbnail !== feed?.thumbnail)
    .slice(0, extra)
    .map((thumbnail, i) => ({
      id: `${creatorId}-p${i + 2}`,
      thumbnail,
      caption: "",
      views: Math.round((feed ? feed.views : 120_000) * (0.62 - i * 0.06)),
      likes: Math.round((feed ? feed.likes : 24_000) * (0.55 - i * 0.05)),
      ...(collabWith && i === 1 ? { collabWith } : {}),
    }));
  return feed ? [{ id: feed.id, thumbnail: feed.thumbnail, caption: feed.caption, views: feed.views, likes: feed.likes }, ...back] : back;
}

// ─── PRIMARY CREATORS ────────────────────────────────────────────────────────
//
// The five creators in the feed, with a full profile each.

const PRIMARY: Creator[] = [
  {
    id: "1", username: "zara.creates", displayName: "Zara Okafor",
    avatarUrl: PORTRAIT("1494790108377-be9c29b29330"), avatarColor: "#00AEEF",
    bio: "Producer & sound designer. I make the beat, you bring the words 🎧 Open to features and remix work.",
    location: "Atlanta, GA", website: "zaracreates.com",
    verified: true, online: true,
    collabStatus: "Available for Collaboration", collabScore: 4.9, collabCount: 312,
    followers: 482_000, following: 318, openToCollab: true, responseTime: "< 4 hours",
    posts: postsFor("1", 0, 8, "nova.dj"),
    playlists: [
      { id: "1-l1", title: "Night Shift Beats", cover: COVER("1598488035139-bdbb2231ce04"), itemLabel: "14 tracks", plays: 892_000 },
      { id: "1-l2", title: "Free Loop Packs", cover: COVER("1511671782779-c97d3d27a1d4"), itemLabel: "22 tracks", plays: 341_000 },
      { id: "1-l3", title: "Sessions w/ friends", cover: COVER("1516280440614-37939bbacd81"), itemLabel: "9 tracks", plays: 128_000 },
    ],
  },
  {
    id: "2", username: "milo.visuals", displayName: "Milo Ferrante",
    avatarUrl: PORTRAIT("1507003211169-0a1dd7228f2d"), avatarColor: "#a78bfa",
    bio: "Photographer chasing light. Brand work, editorial, and the occasional 4am rooftop 📸",
    location: "Lisbon, PT", website: "milo.photo",
    verified: true, online: true,
    collabStatus: "Open to Brand Deals", collabScore: 4.7, collabCount: 184,
    followers: 268_400, following: 512, openToCollab: true, responseTime: "< 24 hours",
    posts: postsFor("2", 1, 8),
    playlists: [
      { id: "2-l1", title: "Golden Hour Series", cover: COVER("1493225457124-a3eb161ffa5f"), itemLabel: "18 stills", plays: 214_000 },
      { id: "2-l2", title: "Behind the Shoot", cover: COVER("1504704911898-68304a7d2807"), itemLabel: "7 episodes", plays: 76_500 },
    ],
  },
  {
    id: "3", username: "nova.dj", displayName: "Nova Reyes",
    avatarUrl: PORTRAIT("1531746020798-e6953c6e8e04"), avatarColor: "#f472b6",
    bio: "DJ / selector. Warehouse sets, festival mainstages, and one very loud studio 🔊",
    location: "Berlin, DE", website: "novareyes.live",
    verified: true, online: true,
    collabStatus: "Available for Collaboration", collabScore: 4.8, collabCount: 521,
    followers: 1_240_000, following: 204, openToCollab: true, responseTime: "< 1 hour",
    posts: postsFor("3", 2, 8, "zara.creates"),
    playlists: [
      { id: "3-l1", title: "HYPERSONIC (Live)", cover: COVER("1516280440614-37939bbacd81"), itemLabel: "11 tracks", plays: 3_400_000 },
      { id: "3-l2", title: "Warehouse Tapes", cover: COVER("1470225620780-dba8ba36b745"), itemLabel: "26 tracks", plays: 1_120_000 },
      { id: "3-l3", title: "Sunrise Sets", cover: COVER("1459749411175-04bf5292ceea"), itemLabel: "8 tracks", plays: 604_000 },
      { id: "3-l4", title: "Unreleased", cover: COVER("1492684223066-81342ee5ff30"), itemLabel: "5 tracks", plays: 88_000 },
    ],
  },
  {
    id: "4", username: "lex.codes", displayName: "Lex Mwangi",
    avatarUrl: PORTRAIT("1539571696357-5a69c17a67c6"), avatarColor: "#22c55e",
    bio: "Building in public. Ship first, sleep later ⚡ Looking for tech sponsors, not managers.",
    location: "Nairobi, KE", website: "lex.codes",
    verified: false, online: false,
    collabStatus: "Seeking Tech Sponsors", collabScore: 4.5, collabCount: 97,
    followers: 96_800, following: 741, openToCollab: true, responseTime: "2–3 days",
    posts: postsFor("4", 3, 5),
    playlists: [
      { id: "4-l1", title: "Weekend Builds", cover: COVER("1611532736597-de2d4265fba3"), itemLabel: "12 episodes", plays: 188_000 },
    ],
  },
  {
    id: "5", username: "ren.filmco", displayName: "Ren Tanaka",
    avatarUrl: PORTRAIT("1524504388940-b1c1722653e1"), avatarColor: "#f59e0b",
    bio: "Cinematographer. 90% light, 10% luck 🎬 Indie features and music videos.",
    location: "Osaka, JP", website: "renfilm.co",
    verified: false, online: false,
    collabStatus: "Available for Collaboration", collabScore: 4.6, collabCount: 238,
    followers: 314_600, following: 189, openToCollab: true, responseTime: "< 24 hours",
    posts: postsFor("5", 4, 8, "milo.visuals"),
    playlists: [
      { id: "5-l1", title: "Shot on $200", cover: COVER("1485846234645-a62644f84728"), itemLabel: "9 films", plays: 470_000 },
      { id: "5-l2", title: "Lighting Breakdowns", cover: COVER("1524650359799-842906ca1c06"), itemLabel: "15 episodes", plays: 210_000 },
    ],
  },
];

// ─── SUPPORTING CREATORS ─────────────────────────────────────────────────────
//
// Everyone who appears in comments, the inbox and live chat. They get real
// profiles too — a handle that can be tapped must lead somewhere — but their
// data is generated from a compact table rather than written out longhand.

interface SeedRow {
  username: string;
  displayName: string;
  photo: string;
  color: string;
  bio: string;
  location: string;
  score: number;
  collabs: number;
  followers: number;
  following: number;
  verified?: boolean;
  online?: boolean;
  openToCollab?: boolean;
}

const SUPPORTING_ROWS: SeedRow[] = [
  { username: "beatsby.kai", displayName: "Kai Brenner", photo: "1500648767791-00dcc994a43e", color: "#00AEEF", bio: "Beats, bass and bad puns. Send stems 🎹", location: "Toronto, CA", score: 4.4, collabs: 76, followers: 48_200, following: 604, online: true },
  { username: "sxundcloud", displayName: "Sam Ade", photo: "1438761681033-6461ffad8d80", color: "#7c3aed", bio: "Curating what you'll be listening to next month.", location: "London, UK", score: 4.2, collabs: 41, followers: 22_900, following: 1_204 },
  { username: "lofi.luna", displayName: "Luna Marchetti", photo: "1544005313-94ddf0286df2", color: "#f472b6", bio: "Lo-fi loops for late deadlines ☕", location: "Milan, IT", score: 4.6, collabs: 118, followers: 132_000, following: 288, verified: true },
  { username: "prod.gio", displayName: "Gio Salas", photo: "1506794778202-cad84cf45f1d", color: "#22c55e", bio: "Producer. I will ask for the stems.", location: "Mexico City, MX", score: 4.1, collabs: 33, followers: 15_400, following: 892 },
  { username: "lens.ivy", displayName: "Ivy Okada", photo: "1534528741775-53994a69daeb", color: "#f59e0b", bio: "Portraits, film grain, honest light.", location: "Vancouver, CA", score: 4.7, collabs: 149, followers: 204_000, following: 341, verified: true, online: true },
  { username: "raw.remi", displayName: "Remi Dubois", photo: "1507003211169-0a1dd7228f2d", color: "#60a5fa", bio: "No presets. No filters. Just RAW.", location: "Paris, FR", score: 4.3, collabs: 58, followers: 61_700, following: 420 },
  { username: "aperture.ax", displayName: "Alex Novak", photo: "1527980965255-d3b416303d12", color: "#a78bfa", bio: "f/1.4 or nothing.", location: "Prague, CZ", score: 4.0, collabs: 22, followers: 9_800, following: 1_050 },
  { username: "drop.dani", displayName: "Dani Cruz", photo: "1438761681033-6461ffad8d80", color: "#ef4444", bio: "Transitions, drops, and questionable neighbours 🔊", location: "Miami, FL", score: 4.3, collabs: 64, followers: 98_300, following: 512, online: true },
  { username: "subwoofer.sz", displayName: "Suzie Zhang", photo: "1500648767791-00dcc994a43e", color: "#0ea5e9", bio: "If it doesn't rattle the windows, re-mix it.", location: "Seoul, KR", score: 4.2, collabs: 37, followers: 33_100, following: 288 },
  { username: "rave.rx", displayName: "Rex Adeyemi", photo: "1544005313-94ddf0286df2", color: "#7dd3fc", bio: "Sunrise sets only.", location: "Lagos, NG", score: 4.1, collabs: 29, followers: 27_600, following: 743 },
  { username: "freq.faye", displayName: "Faye Lindqvist", photo: "1539571696357-5a69c17a67c6", color: "#38bdf8", bio: "Sound design for film and games 🎚", location: "Stockholm, SE", score: 4.3, collabs: 88, followers: 71_200, following: 196, verified: true, online: true },
  { username: "devmo.rei", displayName: "Rei Watanabe", photo: "1539571696357-5a69c17a67c6", color: "#34d399", bio: "Dev tooling and dubious deploy stories.", location: "Tokyo, JP", score: 4.0, collabs: 18, followers: 12_800, following: 604 },
  { username: "build.bex", displayName: "Bex Cole", photo: "1506794778202-cad84cf45f1d", color: "#fb923c", bio: "Teaching the stack I wish I'd been taught.", location: "Austin, TX", score: 4.4, collabs: 52, followers: 44_300, following: 388 },
  { username: "ship.syd", displayName: "Syd Barros", photo: "1527980965255-d3b416303d12", color: "#22d3ee", bio: "Real builders ship 🫡", location: "Lisbon, PT", score: 4.2, collabs: 26, followers: 18_900, following: 511 },
  { username: "film.fee", displayName: "Fee Amara", photo: "1524504388940-b1c1722653e1", color: "#fbbf24", bio: "Lighting is the whole trick.", location: "Cape Town, ZA", score: 4.5, collabs: 94, followers: 88_400, following: 233 },
  { username: "cine.cam", displayName: "Cam Delgado", photo: "1534528741775-53994a69daeb", color: "#c084fc", bio: "Anamorphic apologist.", location: "Madrid, ES", score: 4.3, collabs: 61, followers: 52_700, following: 318 },
  { username: "grade.gus", displayName: "Gus Whitfield", photo: "1500648767791-00dcc994a43e", color: "#4ade80", bio: "Colourist. Yes, that look is a curve.", location: "Auckland, NZ", score: 4.6, collabs: 132, followers: 66_100, following: 174, verified: true },
  { username: "reel.rin", displayName: "Rin Park", photo: "1438761681033-6461ffad8d80", color: "#f9a8d4", bio: "Edits that make you rewatch.", location: "Busan, KR", score: 4.2, collabs: 44, followers: 39_500, following: 622 },
];

const SUPPORTING: Creator[] = SUPPORTING_ROWS.map((row, i) => ({
  id: `s${i + 1}`,
  username: row.username,
  displayName: row.displayName,
  avatarUrl: PORTRAIT(row.photo),
  avatarColor: row.color,
  bio: row.bio,
  location: row.location,
  website: "",
  verified: !!row.verified,
  online: !!row.online,
  collabStatus: row.openToCollab === false ? "Not taking collabs right now" : "Available for Collaboration",
  collabScore: row.score,
  collabCount: row.collabs,
  followers: row.followers,
  following: row.following,
  openToCollab: row.openToCollab !== false,
  responseTime: "< 24 hours",
  posts: postsFor(`s${i + 1}`, i + 5, 6),
  playlists: [],
}));

export const CREATORS: Creator[] = [...PRIMARY, ...SUPPORTING];

// ─── LOOKUPS ─────────────────────────────────────────────────────────────────
//
// Maps rather than `.find()` in render paths: the comment list resolves an
// identity per row, and the feed resolves one per frame.

const BY_ID = new Map(CREATORS.map((c) => [c.id, c]));
const BY_USERNAME = new Map(CREATORS.map((c) => [c.username.toLowerCase(), c]));

export const creatorById = (id: string): Creator | undefined => BY_ID.get(id);

export const creatorByUsername = (username: string): Creator | undefined =>
  BY_USERNAME.get(username.trim().replace(/^@/, "").toLowerCase());

export function registerCreator(input: {
  id: string;
  username: string;
  displayName: string;
  avatarUrl?: string | null;
  avatarColor?: string | null;
  verified?: boolean | null;
  collabScore?: number | null;
  collabCount?: number | null;
  followers?: number | null;
  following?: number | null;
  openToCollab?: boolean | null;
  isFollowing?: boolean | null;
}): Creator {
  const username = input.username.trim().replace(/^@/, "").toLowerCase();
  const existing = BY_ID.get(input.id);
  if (existing) {
    const previousUsername = existing.username.trim().replace(/^@/, "").toLowerCase();
    if (previousUsername !== username && BY_USERNAME.get(previousUsername) === existing) {
      BY_USERNAME.delete(previousUsername);
    }
    existing.username = input.username;
    existing.displayName = input.displayName || input.username;
    existing.avatarUrl = input.avatarUrl ?? existing.avatarUrl;
    existing.avatarColor = input.avatarColor ?? existing.avatarColor;
    existing.verified = input.verified ?? existing.verified;
    existing.collabScore = input.collabScore ?? existing.collabScore;
    existing.collabCount = input.collabCount ?? existing.collabCount;
    existing.followers = input.followers == null
      ? existing.followers
      : input.followers - (input.isFollowing ? 1 : 0);
    existing.following = input.following ?? existing.following;
    existing.openToCollab = input.openToCollab ?? existing.openToCollab;
    BY_USERNAME.set(username, existing);
    return existing;
  }

  const creator: Creator = {
    id: input.id,
    username: input.username,
    displayName: input.displayName || input.username,
    avatarUrl: input.avatarUrl ?? "",
    avatarColor: input.avatarColor ?? "#00AEEF",
    bio: "",
    location: "",
    website: "",
    verified: !!input.verified,
    online: true,
    collabStatus: "Open to Collaboration",
    collabScore: input.collabScore ?? 0,
    collabCount: input.collabCount ?? 0,
    followers: (input.followers ?? 0) - (input.isFollowing ? 1 : 0),
    following: input.following ?? 0,
    openToCollab: input.openToCollab ?? true,
    responseTime: "< 4 hours",
    posts: [],
    playlists: [],
  };
  CREATORS.push(creator);
  BY_ID.set(creator.id, creator);
  BY_USERNAME.set(username, creator);
  return creator;
}

/** For rows that only carry a handle (comments, live chat) and must still render. */
export function identityOf(username: string): { username: string; avatarUrl: string; avatarColor: string; displayName: string } {
  const creator = creatorByUsername(username);
  if (creator) return creator;
  // Unknown handle: no image, and a colour derived from the handle so the
  // initial fallback is at least stable for that person.
  const palette = ["#00AEEF", "#a78bfa", "#22c55e", "#f59e0b", "#f472b6", "#ef4444", "#7c3aed", "#0ea5e9"];
  let hash = 0;
  for (let i = 0; i < username.length; i++) hash = (hash * 31 + username.charCodeAt(i)) % 9973;
  return { username, displayName: username, avatarUrl: "", avatarColor: palette[hash % palette.length] };
}

// ─── THE SIGNED-IN CREATOR ───────────────────────────────────────────────────
//
// The viewer's own numbers. Hard-coded in three places before this — the
// settings card, the holo profile and the analytics page — which is why they
// disagreed. `GET /me/stats` replaces the constant, not the shape.

export const OWN_STATS = {
  followers: 12_400,
  collabScore: 4.8,
  collabCount: 312,
  /** Total views across own posts, shown on the owner's profile only. */
  views: 1_840_000,
};

/** Own posts, so the owner's profile grid is not empty in the prototype. */
export const OWN_POSTS: ContentItem[] = stills(9, 6).map((thumbnail, i) => ({
  id: `me-p${i + 1}`,
  thumbnail,
  caption: "",
  views: Math.round(OWN_STATS.views * (0.22 - i * 0.03)),
  likes: Math.round(OWN_STATS.followers * (1.4 - i * 0.18)),
  ...(i === 0 ? { collabWith: "nova.dj" } : {}),
}));

export const OWN_PLAYLISTS: Playlist[] = [
  { id: "me-l1", title: "Midnight Rush", cover: COVER("1598488035139-bdbb2231ce04"), itemLabel: "8 tracks", plays: 214_000 },
  { id: "me-l2", title: "Studio Diaries", cover: COVER("1504704911898-68304a7d2807"), itemLabel: "6 episodes", plays: 64_800 },
];
