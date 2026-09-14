// ─── SETTINGS DESTINATIONS ───────────────────────────────────────────────────
//
// This file used to hold every settings page. Those have moved to
// `src/app/pages/`, each destination in its own component file. This module now
// only wires them into the `SettingsRoute` → component table that
// `Settings.tsx` consumes, so `SettingsPages` keeps its public surface for
// existing importers while the implementation lives with the other pages.
//
// Two save models are used deliberately across the pages:
//   • Forms that can fail validation (profile, password) have an explicit
//     submit button and surface the store's error.
//   • Preference screens write on every tap and confirm with `SavedPill`, so a
//     user who backs out never silently loses a change.

import type { ReactElement } from "react";
import {
  type SettingsRoute, type PageProps,
  EditProfilePage, ChangePasswordPage, NotificationsPage, PrivacyPage,
  CollabPreferencesPage, ResponseTimePage, PortfolioPage, AnalyticsPage,
  PlatformAnalyticsPage,
  HelpCenterPage, ReportProblemPage, TermsPage, PrivacyPolicyPage,
} from "./pages";

// ─── ROUTE TABLE ─────────────────────────────────────────────────────────────
//
// Exhaustive by construction: `SettingsRoute` and this record are typed
// together, so a route added without a page is a compile error rather than a
// row that silently does nothing. Components are re-exported for convenience.

export type { SettingsRoute, PageProps } from "./pages";

export const SETTINGS_PAGES: Record<SettingsRoute, (props: PageProps) => ReactElement> = {
  editProfile: EditProfilePage,
  changePassword: ChangePasswordPage,
  notifications: NotificationsPage,
  privacy: PrivacyPage,
  collabPreferences: CollabPreferencesPage,
  responseTime: ResponseTimePage,
  portfolio: PortfolioPage,
  analytics: AnalyticsPage,
  platformAnalytics: PlatformAnalyticsPage,
  helpCenter: HelpCenterPage,
  reportProblem: ReportProblemPage,
  terms: TermsPage,
  privacyPolicy: PrivacyPolicyPage,
};