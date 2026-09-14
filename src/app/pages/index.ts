// ─── PAGES BARREL ────────────────────────────────────────────────────────────
//
// Centralises all settings destination components in `src/app/pages`.
// `SettingsPages.tsx` re-exports from here so external callers (e.g.
// `Settings.tsx`) keep their existing import unchanged.

export type { SettingsRoute, PageProps } from "./settingsPages.types";
export { EditProfilePage } from "./EditProfilePage";
export { ChangePasswordPage } from "./ChangePasswordPage";
export { NotificationsPage } from "./NotificationsPage";
export { PrivacyPage } from "./PrivacyPage";
export { CollabPreferencesPage } from "./CollabPreferencesPage";
export { ResponseTimePage } from "./ResponseTimePage";
export { PortfolioPage } from "./PortfolioPage";
export { AnalyticsPage } from "./AnalyticsPage";
export { PlatformAnalyticsPage } from "./PlatformAnalyticsPage";
export { HelpCenterPage } from "./HelpCenterPage";
export { ReportProblemPage } from "./ReportProblemPage";
export { TermsPage, PrivacyPolicyPage } from "./LegalPages";