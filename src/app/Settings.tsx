// ─── SETTINGS ────────────────────────────────────────────────────────────────
//
// The settings list and the stack it pushes onto. Every row here resolves to a
// `SettingsRoute` in `SettingsPages.tsx`; the two destructive rows (Log Out,
// Delete Profile) call back up to the app instead, because they end the session.

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ArrowLeft, ChevronRight, X, AlertCircle, Loader2, Star } from "lucide-react";
import { type Account, profileOf, hasPassword, deleteAccount } from "./auth-store";
import {
  type Preferences, loadPreferences, savePreferences, clearPreferences,
} from "./settings-store";
import { ACCENT, useTokens, SavedPill } from "./settings-ui";
import { type SettingsRoute, SETTINGS_PAGES } from "./SettingsPages";

// ─── LIST DEFINITION ─────────────────────────────────────────────────────────
//
// Rows are data, not markup, so every one is guaranteed to carry a route — the
// original bug was a row rendered without a handler, which this shape prevents.

interface SettingsRow {
  route: SettingsRoute;
  label: string;
  /** Right-hand summary of the current value, when there is one worth showing. */
  value?: (ctx: { account: Account; prefs: Preferences }) => string;
}

interface SettingsSection {
  title: string;
  rows: SettingsRow[];
}

const SECTIONS: SettingsSection[] = [
  {
    title: "Account",
    rows: [
      { route: "editProfile", label: "Edit Profile" },
      {
        route: "changePassword",
        label: "Change Password",
        // Provider-only accounts have no password yet, so the row offers to set one.
        value: ({ account }) => (hasPassword(account) ? "" : "Not set"),
      },
      { route: "notifications", label: "Notification Preferences" },
      {
        route: "privacy",
        label: "Privacy Settings",
        value: ({ prefs }) => (prefs.privacy.privateAccount ? "Private" : "Public"),
      },
    ],
  },
  {
    title: "Creator",
    rows: [
      {
        route: "collabPreferences",
        label: "Collab Preferences",
        value: ({ prefs }) => (prefs.collab.openToCollab ? `${prefs.collab.types.length} types` : "Closed"),
      },
      { route: "responseTime", label: "Response Time", value: ({ prefs }) => prefs.responseTime },
      { route: "portfolio", label: "Portfolio", value: ({ prefs }) => String(prefs.portfolio.length) },
      { route: "analytics", label: "Analytics" },
    ],
  },
  {
    title: "Support",
    rows: [
      { route: "helpCenter", label: "Help Center" },
      { route: "reportProblem", label: "Report a Problem" },
      { route: "terms", label: "Terms of Service" },
      { route: "privacyPolicy", label: "Privacy Policy" },
    ],
  },
];

const ADMIN_SECTIONS: SettingsSection[] = [
  { title: "Administration", rows: [{ route: "platformAnalytics", label: "Platform Analytics" }] },
];

// ─── SETTINGS SCREEN ─────────────────────────────────────────────────────────

export function SettingsScreen({
  account, onBack, onLogout, onDeleteProfile, onAccountChange, isDark = true, onToggleTheme,
}: {
  account: Account;
  onBack: () => void;
  onLogout: () => void;
  onDeleteProfile: () => void;
  /** Called after a profile or password edit so the app holds the fresh account. */
  onAccountChange: (account: Account) => void;
  isDark?: boolean;
  onToggleTheme?: () => void;
}) {
  const t = useTokens(isDark);
  const profile = profileOf(account);
  const sections = account.role === "admin" ? [...SECTIONS, ...ADMIN_SECTIONS] : SECTIONS;

  // A stack, not a single route: pages cross-link to each other (Help Center →
  // Report a Problem, Collab Preferences → Response Time), and Back has to
  // return to where you came from rather than always to the list.
  const [stack, setStack] = useState<SettingsRoute[]>([]);
  const [prefs, setPrefs] = useState<Preferences>(() => loadPreferences(account.email));
  const [savedPill, setSavedPill] = useState(false);
  const pillTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Signing in as someone else must not inherit the previous user's settings.
  useEffect(() => { setPrefs(loadPreferences(account.email)); }, [account.email]);

  useEffect(() => () => { if (pillTimer.current) clearTimeout(pillTimer.current); }, []);

  const patchPrefs = useCallback((patch: Partial<Preferences>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch };
      savePreferences(account.email, next);
      return next;
    });
    setSavedPill(true);
    if (pillTimer.current) clearTimeout(pillTimer.current);
    pillTimer.current = setTimeout(() => setSavedPill(false), 1400);
  }, [account.email]);

  const push = useCallback((next: SettingsRoute) => setStack((s) => [...s, next]), []);
  const pop = useCallback(() => setStack((s) => s.slice(0, -1)), []);

  const route = stack[stack.length - 1] ?? null;
  const ActivePage = route ? SETTINGS_PAGES[route] : null;

  return (
    <div className="absolute inset-0 z-20 overflow-hidden" style={{ background: t.bg }}>
      <div className="absolute inset-0 overflow-y-auto">
        <div className="px-5 pt-14 pb-10">
          <div className="flex items-center gap-3 mb-6">
            <button onClick={onBack} aria-label="Back"
              className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 transition-opacity active:opacity-70"
              style={{ background: t.backBtnBg, border: t.cardBorder }}>
              <ArrowLeft className="w-4 h-4" style={{ color: t.heading }} />
            </button>
            <h1 className="font-extrabold text-[26px]" style={{ color: t.heading }}>Settings</h1>
          </div>

          {/* Profile card — doubles as the shortcut into Edit Profile. */}
          <button onClick={() => push("editProfile")}
            className="w-full flex items-center gap-4 p-4 rounded-2xl mb-6 text-left transition-opacity active:opacity-70"
            style={{ background: t.cardBg, border: t.cardBorder }}>
            <div className="w-14 h-14 rounded-full flex items-center justify-center text-white text-xl font-bold flex-shrink-0"
              style={{ background: profile.avatarColor }}>
              {(profile.displayName || profile.username || "?")[0].toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="font-bold text-[16px] truncate" style={{ color: t.heading }}>@{profile.username}</p>
              <p className="text-[13px] truncate" style={{ color: t.sub }}>{account.email}</p>
              {prefs.privacy.showCollabScore && (
                <div className="flex items-center gap-1.5 mt-1.5">
                  <Star className="w-3 h-3" style={{ color: ACCENT, fill: ACCENT }} />
                  <span className="text-[12px] font-bold" style={{ color: ACCENT }}>4.8</span>
                  <span className="text-[12px]" style={{ color: t.sub }}>· 312 collabs</span>
                </div>
              )}
            </div>
            <ChevronRight className="w-4 h-4 flex-shrink-0" style={{ color: t.chevron }} />
          </button>

          {/* Appearance */}
          <div className="mb-5">
            <p className="text-[11px] font-bold uppercase tracking-widest mb-2 px-1" style={{ color: t.sectionLbl }}>Appearance</p>
            <div className="rounded-2xl overflow-hidden" style={{ background: t.groupBg, border: t.groupBorder }}>
              <button onClick={onToggleTheme} className="w-full flex items-center justify-between px-4 py-4 text-left">
                <div className="flex items-center gap-3">
                  <span className="text-lg">{isDark ? "🌙" : "☀️"}</span>
                  <span className="text-[14px]" style={{ color: t.body }}>{isDark ? "Dark Mode" : "Light Mode"}</span>
                </div>
                <div className="w-12 h-6 rounded-full relative transition-colors flex-shrink-0"
                  style={{ background: isDark ? ACCENT : t.switchOff }}>
                  <motion.div animate={{ x: isDark ? 24 : 2 }} transition={{ type: "spring", damping: 20 }}
                    className="absolute top-1 w-4 h-4 rounded-full bg-white shadow-sm" style={{ left: 0 }} />
                </div>
              </button>
            </div>
          </div>

          {/* Navigable sections */}
          {sections.map((sec) => (
            <div key={sec.title} className="mb-5">
              <p className="text-[11px] font-bold uppercase tracking-widest mb-2 px-1" style={{ color: t.sectionLbl }}>{sec.title}</p>
              <div className="rounded-2xl overflow-hidden" style={{ background: t.groupBg, border: t.groupBorder }}>
                {sec.rows.map((row, i) => {
                  const value = row.value?.({ account, prefs });
                  return (
                    <button key={row.route} onClick={() => push(row.route)}
                      className="w-full flex items-center justify-between px-4 py-4 text-left transition-opacity active:opacity-70"
                      style={{ borderBottom: i < sec.rows.length - 1 ? `1px solid ${t.divider}` : "none" }}>
                      <span className="text-[14px]" style={{ color: t.body }}>{row.label}</span>
                      <span className="flex items-center gap-2 flex-shrink-0">
                        {value && <span className="text-[13px]" style={{ color: t.sub }}>{value}</span>}
                        <ChevronRight className="w-4 h-4" style={{ color: t.chevron }} />
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {/* Destructive rows — these end the session, so the app owns them. */}
          <div className="mb-3">
            <div className="rounded-2xl overflow-hidden" style={{ background: t.groupBg, border: t.groupBorder }}>
              <motion.button whileTap={{ scale: 0.98 }} onClick={onLogout}
                className="w-full flex items-center justify-between px-4 py-4"
                style={{ borderBottom: `1px solid ${t.divider}` }}>
                <span className="text-[14px]" style={{ color: t.body }}>Log Out</span>
                <ChevronRight className="w-4 h-4" style={{ color: t.chevron }} />
              </motion.button>
              <motion.button whileTap={{ scale: 0.98 }} onClick={onDeleteProfile}
                className="w-full flex items-center justify-between px-4 py-4">
                <span className="text-red-400 text-[14px] font-semibold">Delete Profile</span>
                <ChevronRight className="w-4 h-4 text-red-400/40" />
              </motion.button>
            </div>
          </div>

          <p className="text-center text-[12px] mt-6" style={{ color: t.sub }}>ConnextionZ v1.0.0</p>
        </div>
      </div>

      {/* Destination stack */}
      <AnimatePresence>
        {ActivePage && (
          <ActivePage
            key={`${stack.length}-${route}`}
            account={account}
            prefs={prefs}
            t={t}
            onBack={pop}
            onPatch={patchPrefs}
            onAccountChange={onAccountChange}
            onNavigate={push}
          />
        )}
      </AnimatePresence>

      <SavedPill show={savedPill} />
    </div>
  );
}

// ─── DELETE PROFILE MODAL ────────────────────────────────────────────────────

export function DeleteProfileModal({
  account, onDeleted, onCancel,
}: { account: Account; onDeleted: () => void; onCancel: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    setLoading(true);
    setError(null);
    const result = await deleteAccount(account.email);
    setLoading(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // Preferences are keyed separately from the account, so both are cleared.
    clearPreferences(account.email);
    onDeleted();
  };

  if (!confirm) {
    return (
      <motion.div initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 40 }}
        className="absolute inset-x-4 bottom-4 top-4 rounded-3xl z-50 flex flex-col overflow-hidden"
        style={{ background: "linear-gradient(160deg,#00091e,#000d28)", border: "1px solid rgba(0,174,239,0.2)", boxShadow: "0 -20px 60px rgba(0,0,0,0.8)" }}>
        <div className="flex justify-center pt-3 pb-1 flex-shrink-0"><div className="w-9 h-1 rounded-full bg-white/20" /></div>

        <div className="flex-1 overflow-y-auto px-6 pb-8 pt-4 space-y-6">
          <div className="flex flex-col items-center text-center space-y-4 pt-4">
            <div className="w-20 h-20 rounded-full flex items-center justify-center" style={{ background: "rgba(239,68,68,0.12)", border: "2px solid rgba(239,68,68,0.3)" }}>
              <AlertCircle className="w-9 h-9 text-red-400" />
            </div>
            <div>
              <h2 className="text-white font-extrabold text-[24px]">Delete Profile?</h2>
              <p className="text-white/50 text-[14px] mt-2 leading-relaxed">This action is permanent and cannot be undone.</p>
            </div>
          </div>

          <div className="rounded-2xl p-4 space-y-3" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}>
            <p className="text-red-400 text-[12px] font-bold uppercase tracking-widest">What will be deleted</p>
              {["Your profile and creator identity", "Your posts and playlists", "Uploaded avatars and media", "Follows and likes", "Every preference you have set"].map((item) => (
              <div key={item} className="flex items-center gap-3">
                <X className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                <span className="text-white/65 text-[13px]">{item}</span>
              </div>
            ))}
          </div>
          {error && <p className="text-red-300 text-[13px] text-center">{error}</p>}

          <div className="flex gap-3 pt-2">
            <motion.button whileTap={{ scale: 0.97 }} onClick={onCancel}
              className="flex-1 py-4 rounded-full font-bold text-[15px] text-white/80"
              style={{ background: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.12)" }}>
              Cancel
            </motion.button>
            <motion.button whileTap={{ scale: 0.97 }} onClick={() => setConfirm(true)}
              className="flex-1 py-4 rounded-full font-bold text-[15px] text-white"
              style={{ background: "rgba(239,68,68,0.2)", border: "1px solid rgba(239,68,68,0.4)" }}>
              Continue
            </motion.button>
          </div>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 40 }}
      className="absolute inset-x-4 bottom-4 rounded-3xl z-50 overflow-hidden"
      style={{ background: "#16161a", border: "1px solid rgba(239,68,68,0.3)", boxShadow: "0 -20px 60px rgba(0,0,0,0.8)" }}>
      <div className="px-6 pt-6 pb-8 space-y-5">
        <div className="text-center space-y-2">
          <h2 className="text-white font-extrabold text-[22px]">Final confirmation</h2>
          <p className="text-white/45 text-[13px]">Type <span className="font-bold text-red-400">DELETE</span> to confirm</p>
        </div>
        <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type DELETE here"
          className="w-full rounded-2xl text-white text-[15px] outline-none placeholder:text-white/20 px-4 py-3.5 text-center"
          style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)" }} />
        <div className="flex gap-3">
          <motion.button whileTap={{ scale: 0.97 }} onClick={() => setConfirm(false)}
            className="flex-1 py-3.5 rounded-full font-bold text-[14px] text-white/70"
            style={{ background: "rgba(0,60,130,0.35)", border: "1px solid rgba(0,174,239,0.15)" }}>
            Back
          </motion.button>
          <motion.button whileTap={{ scale: 0.97 }} onClick={handleDelete} disabled={typed !== "DELETE" || loading}
            className="flex-1 py-3.5 rounded-full font-bold text-[14px] text-white flex items-center justify-center gap-2"
            style={{ background: typed === "DELETE" ? "rgba(239,68,68,0.85)" : "rgba(239,68,68,0.15)", opacity: loading ? 0.8 : 1 }}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Delete Profile"}
          </motion.button>
        </div>
      </div>
    </motion.div>
  );
}
