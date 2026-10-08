// ─── SETTINGS STORE ──────────────────────────────────────────────────────────
//
// Everything the Settings screens read and write, persisted per account so
// signing in as someone else does not inherit the previous user's preferences.
//
// Preferences remain cached here for the synchronous Settings UI. The
// non-category onboarding fields are persisted to the authenticated profile
// and this cache is refreshed from the server during onboarding.
//
// ── Replacing this with a real backend ──────────────────────────────────────
// Keep the exported signatures and the Settings UI needs no changes:
//
//   loadPreferences()  → GET  /me/preferences
//   savePreferences()  → PATCH /me/preferences
//   clearPreferences() → handled server-side by account deletion

const PREFS_KEY = "connextionz.preferences";

// ─── SHAPES ──────────────────────────────────────────────────────────────────

export interface NotificationPrefs {
  collabRequests: boolean;
  messages: boolean;
  likes: boolean;
  comments: boolean;
  newFollowers: boolean;
  liveAlerts: boolean;
  trendingSounds: boolean;
  productUpdates: boolean;
  /** Email digest cadence — "off" means transactional mail only. */
  emailDigest: "off" | "daily" | "weekly";
  /** Mutes push between 22:00 and 08:00 when on. */
  quietHours: boolean;
}

export type Audience = "everyone" | "followers" | "nobody";

export interface PrivacyPrefs {
  privateAccount: boolean;
  whoCanMessage: Audience;
  whoCanCollab: Audience;
  showCollabScore: boolean;
  showOnlineStatus: boolean;
  allowMentions: boolean;
  /** Whether the profile is surfaced in Discover and search results. */
  discoverable: boolean;
  /** Opt-in to personalised recommendations built from activity. */
  personalisation: boolean;
}

export interface CollabPrefs {
  /** Labels from the onboarding collab-type list. */
  types: string[];
  budget: string;
  /** Only accept requests that can be done remotely. */
  remoteOnly: boolean;
  /** Auto-decline requests below `minCollabScore`. */
  autoScreen: boolean;
  minCollabScore: number;
  openToCollab: boolean;
}

export interface PortfolioItem {
  id: string;
  title: string;
  role: string;
  year: string;
  /** A `thumbnail`-style image URL, or "" for the generated placeholder. */
  image: string;
  featured: boolean;
}

export interface Preferences {
  notifications: NotificationPrefs;
  privacy: PrivacyPrefs;
  collab: CollabPrefs;
  /** One of `RESPONSE_TIME_OPTIONS`. */
  responseTime: string;
  portfolio: PortfolioItem[];
  /** Interests picked during onboarding, editable from Collab Preferences. */
  categories: string[];
}

export const RESPONSE_TIME_OPTIONS = [
  { value: "< 1 hour", label: "Within an hour", sub: "Fastest — signals you're highly available" },
  { value: "< 4 hours", label: "Within 4 hours", sub: "A solid same-day reply window" },
  { value: "< 24 hours", label: "Within a day", sub: "Standard for most creators" },
  { value: "2–3 days", label: "2–3 days", sub: "For when you batch your inbox" },
];

export const BUDGET_OPTIONS = ["Under $500", "$500–$2K", "$2K–$10K", "$10K+", "Open to discuss"];

export const AUDIENCE_OPTIONS: { value: Audience; label: string }[] = [
  { value: "everyone", label: "Everyone" },
  { value: "followers", label: "People I follow back" },
  { value: "nobody", label: "No one" },
];

// ─── DEFAULTS ────────────────────────────────────────────────────────────────

export const DEFAULT_PREFERENCES: Preferences = {
  notifications: {
    collabRequests: true,
    messages: true,
    likes: true,
    comments: true,
    newFollowers: false,
    liveAlerts: true,
    trendingSounds: false,
    productUpdates: false,
    emailDigest: "weekly",
    quietHours: false,
  },
  privacy: {
    privateAccount: false,
    whoCanMessage: "everyone",
    whoCanCollab: "everyone",
    showCollabScore: true,
    showOnlineStatus: true,
    allowMentions: true,
    discoverable: true,
    personalisation: true,
  },
  collab: {
    types: ["Paid Collaboration", "Free / Creative Collab"],
    budget: "$500–$2K",
    remoteOnly: false,
    autoScreen: false,
    minCollabScore: 4.0,
    openToCollab: true,
  },
  responseTime: "< 4 hours",
  portfolio: [
    {
      id: "p1", title: "Midnight Rush", role: "Producer · with @nova.dj", year: "2026",
      image: "https://images.unsplash.com/photo-1598488035139-bdbb2231ce04?w=400&h=400&fit=crop&auto=format",
      featured: true,
    },
    {
      id: "p2", title: "Golden Hour Series", role: "Cinematography · with @milo.visuals", year: "2025",
      image: "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=400&h=400&fit=crop&auto=format",
      featured: false,
    },
    {
      id: "p3", title: "Build In Public", role: "Co-host · 12-episode series", year: "2025",
      image: "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=400&h=400&fit=crop&auto=format",
      featured: false,
    },
  ],
  categories: [],
};

// ─── STORAGE ─────────────────────────────────────────────────────────────────

type PrefsByAccount = Record<string, Preferences>;

const key = (email: string) => email.trim().toLowerCase();

function readAll(): PrefsByAccount {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as PrefsByAccount) : {};
  } catch {
    return {};
  }
}

function writeAll(all: PrefsByAccount) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(all));
  } catch {
    /* Non-fatal: storage disabled means preferences just will not persist. */
  }
}

/**
 * Merges stored values over the defaults one group at a time, so a preference
 * added in a later release is populated for accounts saved before it existed.
 */
export function loadPreferences(email: string): Preferences {
  const stored = readAll()[key(email)];
  if (!stored) return structuredClone(DEFAULT_PREFERENCES);
  return {
    notifications: { ...DEFAULT_PREFERENCES.notifications, ...stored.notifications },
    privacy: { ...DEFAULT_PREFERENCES.privacy, ...stored.privacy },
    collab: { ...DEFAULT_PREFERENCES.collab, ...stored.collab },
    responseTime: stored.responseTime ?? DEFAULT_PREFERENCES.responseTime,
    portfolio: Array.isArray(stored.portfolio) ? stored.portfolio : DEFAULT_PREFERENCES.portfolio,
    categories: Array.isArray(stored.categories) ? stored.categories : [],
  };
}

export function savePreferences(email: string, prefs: Preferences) {
  const all = readAll();
  all[key(email)] = prefs;
  writeAll(all);
}

export function clearPreferences(email: string) {
  const all = readAll();
  delete all[key(email)];
  writeAll(all);
}
