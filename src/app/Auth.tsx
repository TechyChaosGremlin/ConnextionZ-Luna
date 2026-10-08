import { useCallback, useState }                                from "react";
import { motion, AnimatePresence }                              from "motion/react";
import { Check, AlertCircle }                                   from "lucide-react";
import { updateProfile, profileOf, startSession, type Account } from "./auth-store";
import { updateMyOnboardingPreferences } from "./profile-graphql";
import { saveMyOnboardingCategories } from "./onboarding-store";
import { loadPreferences, savePreferences }                     from "./settings-store";

import BrandPanel                      from "./components/BrandPanel"
import CreateAccount                   from "./components/CreateAccount"
import ForgotPassword                  from "./components/ForgotPassword"
import GetStarted                      from "./components/GetStarted"
import Login                           from "./components/Login"
import Onboarding, { OnboardingSetup } from "./components/Onboarding"
import ResetPassword                   from "./components/ResetPassword"
import ResetSent                       from "./components/ResetSent"

// ─── TYPES ───────────────────────────────────────────────────────────────────

export type Screen =
  | "getStarted"
  | "login"
  | "createAccount"
  | "forgotPassword"
  | "resetSent"
  | "resetPassword"
  | "onboarding";

export type OnbStep = 1 | 2 | 3;

// ─── RESPONSIVE LAYOUT TOKENS ────────────────────────────────────────────────
// Mobile/tablet (< lg) keeps the verified Figma proportions untouched.
// From `lg` (1024px) up, the auth flow becomes a two-column desktop layout:
// a brand panel on the left and this content column on the right, so screens
// stop stretching edge-to-edge on wide viewports.

/** Screen root: fills the frame on mobile, becomes an auto-height column on desktop. */
export const SCREEN = "flex flex-col h-full lg:h-auto";
/** Horizontal gutter — the desktop shell supplies its own padding. */
export const GUTTER = "px-6 lg:px-0";
/** Status-bar offset, only meaningful on mobile. */
export const TOP_PAD = "pt-14 lg:pt-0";
/** Page heading. */
export const H1 = "text-white font-extrabold text-[30px] lg:text-[36px] leading-tight";
/** Sub-heading under an H1. */
export const SUB = "text-white/45 text-[15px] lg:text-[16px]";

// ─── INPUT ───────────────────────────────────────────────────────────────────

interface InputProps {
  label?: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  error?: string;
  icon?: React.ReactNode;
  rightEl?: React.ReactNode;
  autoFocus?: boolean;
}

export function Input({ label, type = "text", value, onChange, placeholder, error, icon, rightEl, autoFocus }: InputProps) {
  return (
    <div className="space-y-1.5">
      {label && <p className="text-white/50 text-[12px] font-semibold uppercase tracking-widest px-1">{label}</p>}
      <div className="relative">
        {icon && (
          <div className="absolute left-4 top-1/2 -translate-y-1/2 text-white/35 pointer-events-none">
            {icon}
          </div>
        )}
        <input
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoFocus={autoFocus}
          className={[
            "w-full rounded-2xl text-white text-[15px] lg:text-[16px] outline-none transition-all placeholder:text-white/25",
            "py-3.5 lg:py-4",
            icon ? "pl-11" : "pl-4",
            rightEl ? "pr-12" : "pr-4",
          ].join(" ")}
          style={{
            background: "rgba(0,80,160,0.12)",
            border: error ? "1px solid rgba(239,68,68,0.6)" : "1px solid rgba(0,174,239,0.18)",
          }}
          onFocus={(e) => { e.target.style.border = `1px solid rgba(0,174,239,0.5)`; }}
          onBlur={(e) => { e.target.style.border = error ? "1px solid rgba(239,68,68,0.6)" : "1px solid rgba(0,174,239,0.18)"; }}
        />
        {rightEl && (
          <div className="absolute right-4 top-1/2 -translate-y-1/2">
            {rightEl}
          </div>
        )}
      </div>
      <AnimatePresence>
        {error && (
          <motion.p
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            className="text-red-400 text-[12px] flex items-center gap-1 px-1"
          >
            <AlertCircle className="w-3 h-3 flex-shrink-0" /> {error}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── CHECKBOX ────────────────────────────────────────────────────────────────

export function Checkbox({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <button onClick={() => onChange(!checked)} className="flex items-start gap-3 text-left">
      <motion.div
        animate={{ background: checked ? "#00AEEF" : "rgba(0,60,130,0.3)", borderColor: checked ? "#00AEEF" : "rgba(0,174,239,0.3)" }}
        className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0 mt-0.5 border"
        style={{ border: "1.5px solid rgba(0,174,239,0.3)" }}
      >
        <AnimatePresence>
          {checked && (
            <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }} transition={{ type: "spring", damping: 16 }}>
              <Check className="w-3 h-3 text-black" strokeWidth={3} />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
      <span className="text-white/60 text-[13px] leading-relaxed">{children}</span>
    </button>
  );
}

// ─── EMAIL VALIDATION ────────────────────────────────────────────────────────
export const isValidEmail = (e: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e);

// ─── AUTH FLOW (ROOT) ─────────────────────────────────────────────────────────

export function AuthFlow({ onAuthenticated }: { onAuthenticated: (account: Account) => void }) {
  const [screen, setScreen] = useState<Screen>("getStarted");
  /** Carried between the forgot-password, reset-sent and set-password screens. */
  const [resetEmail, setResetEmail] = useState("");
  const [resetToken, setResetToken] = useState<string | null>(null);
  /** The account being onboarded — onboarding needs somewhere to write its picks. */
  const [pending, setPending] = useState<Account | null>(null);
  const [onboardingError, setOnboardingError] = useState<string | null>(null);

  /** Opens the session and hands the account to the app. */
  const enter = useCallback((account: Account) => {
    startSession(account.email);
    onAuthenticated(account);
  }, [onAuthenticated]);

  /**
   * Persists what onboarding collected before entering the app, so Settings
   * opens already reflecting the user's picks rather than defaults.
   */
  const completeOnboarding = useCallback(async (setup: OnboardingSetup | null) => {
    const account = pending;
    if (!account) return;
    if (!setup) { enter(account); return; }

    setOnboardingError(null);
    const savedPreferences = await updateMyOnboardingPreferences({
      collabTypes: setup.collabTypes,
      responseTime: setup.responseTime,
      openToCollab: setup.openToCollab,
    });
    if (!savedPreferences.ok) {
      setOnboardingError(savedPreferences.error);
      return;
    }

    const savedCategories = await saveMyOnboardingCategories(account.email, setup.categories);
    if (!savedCategories.ok) {
      setOnboardingError(savedCategories.error);
      return;
    }

    const prefs = loadPreferences(account.email);
    savePreferences(account.email, {
      ...prefs,
      responseTime: savedPreferences.value.responseTime,
      collab: {
        ...prefs.collab,
        types: savedPreferences.value.collabTypes,
        openToCollab: savedPreferences.value.openToCollab,
      },
    });

    const result = await updateProfile(account.email, {
      displayName: setup.creatorName || profileOf(account).displayName,
      avatarColor: setup.avatarColor,
    });
    enter(result.ok ? result.value : account);
  }, [pending, enter]);

  const slide = { initial: { opacity: 0, x: 40 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -40 } };
  const slideUp = { initial: { opacity: 0, y: 40 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: 20 } };
  const trans = { duration: 0.22 };

  // < lg: a single full-bleed column (unchanged mobile/tablet behaviour).
  // ≥ lg: brand panel + content column, so the form keeps a readable measure
  //       while the layout still fills the full desktop width.
  const pane = "h-full lg:h-auto";

  return (
    <div className="h-full w-full overflow-hidden" style={{ background: "linear-gradient(160deg, #00091a 0%, #000d24 40%, #000814 100%)", fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif" }}>
      <div className="h-full w-full lg:grid lg:grid-cols-2 2xl:grid-cols-[1.2fr_1fr]">
        <BrandPanel screen={screen} />

        <div className="relative h-full overflow-hidden lg:border-l lg:border-[rgba(0,174,239,0.14)]">
          {/* Soft accent so the form column has the same depth as the brand panel. */}
          <div
            className="hidden lg:block absolute -right-40 top-1/2 -translate-y-1/2 w-[560px] h-[560px] rounded-full pointer-events-none"
            style={{ background: "radial-gradient(circle, rgba(0,174,239,0.10) 0%, transparent 70%)" }}
          />
          {/* Scroll container is a plain block: centring lives on the inner wrapper via
              min-h-full, so a form taller than the viewport stays fully reachable. */}
          <div className="relative h-full lg:overflow-y-auto">
            <div className="h-full lg:h-auto lg:min-h-full lg:flex lg:items-center lg:justify-center lg:px-10 xl:px-16 lg:py-14">
              <div className="h-full w-full lg:h-auto lg:max-w-[460px]">
                <AnimatePresence mode="wait">
                  {screen === "getStarted" && (
                    <motion.div key="gs" {...slideUp} transition={trans} className={pane}>
                      <GetStarted onGetStarted={() => setScreen("createAccount")} onLogin={() => setScreen("login")} />
                    </motion.div>
                  )}
                  {screen === "login" && (
                    <motion.div key="li" {...slide} transition={trans} className={pane}>
                      <Login onLogin={enter} onCreate={() => setScreen("createAccount")} onForgot={() => setScreen("forgotPassword")} onBack={() => setScreen("getStarted")} />
                    </motion.div>
                  )}
                  {screen === "createAccount" && (
                    <motion.div key="ca" {...slide} transition={trans} className={pane}>
                      <CreateAccount onCreated={(account) => { setPending(account); setScreen("onboarding"); }}
                        onLogin={() => setScreen("login")} onBack={() => setScreen("getStarted")} />
                    </motion.div>
                  )}
                  {screen === "forgotPassword" && (
                    <motion.div key="fp" {...slide} transition={trans} className={pane}>
                      <ForgotPassword
                        onSent={(email, token) => { setResetEmail(email); setResetToken(token); setScreen("resetSent"); }}
                        onBack={() => setScreen("login")} />
                    </motion.div>
                  )}
                  {screen === "resetSent" && (
                    <motion.div key="rs" {...slideUp} transition={trans} className={pane}>
                      <ResetSent
                        email={resetEmail}
                        token={resetToken}
                        onOpenLink={() => setScreen("resetPassword")}
                        onResend={(token) => setResetToken(token)}
                        onBackToLogin={() => setScreen("login")} />
                    </motion.div>
                  )}
                  {screen === "resetPassword" && (
                    <motion.div key="rp" {...slide} transition={trans} className={pane}>
                      <ResetPassword
                        token={resetToken ?? ""}
                        onDone={() => setScreen("login")}
                        onBack={() => setScreen("forgotPassword")} />
                    </motion.div>
                  )}
                  {screen === "onboarding" && (
                    <motion.div key="ob" {...slideUp} transition={trans} className={pane}>
                      <Onboarding onDone={completeOnboarding} error={onboardingError} />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
