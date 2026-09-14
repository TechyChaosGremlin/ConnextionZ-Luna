// ─── SETTINGS PAGE TYPES ────────────────────────────────────────────────────
//
// Shared route + component props for every settings destination. Kept in its
// own file so `Settings.tsx` and the page components can all import from one
// place without pulling in each other's internals.

import type { Account } from "../auth-store";
import type { Preferences } from "../settings-store";
import type { Tokens } from "../settings-ui";

export type SettingsRoute =
  | "editProfile"
  | "changePassword"
  | "notifications"
  | "privacy"
  | "collabPreferences"
  | "responseTime"
  | "portfolio"
  | "analytics"
  | "platformAnalytics"
  | "helpCenter"
  | "reportProblem"
  | "terms"
  | "privacyPolicy";

export interface PageProps {
  account: Account;
  prefs: Preferences;
  t: Tokens;
  onBack: () => void;
  /** Persists a preference patch and flashes the "saved" pill. */
  onPatch: (patch: Partial<Preferences>) => void;
  /** Propagates an account change (profile edit, password change) upward. */
  onAccountChange: (account: Account) => void;
  /** Cross-links between destinations, e.g. Help Center → Report a Problem. */
  onNavigate: (route: SettingsRoute) => void;
}