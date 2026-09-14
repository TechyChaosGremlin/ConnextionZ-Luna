import { AUTH_LOGIN_ENDPOINT, GRAPHQL_ENDPOINT } from "./api-config";
import { getProfileValidationError, normalizeProfilePatch } from "./profile-validation";

// ─── ACCOUNT STORE ───────────────────────────────────────────────────────────
//
// ⚠️  PROTOTYPE CREDENTIAL STUB — THIS IS NOT AUTHENTICATION.
//
// This module exists so the UI behaves correctly: wrong passwords are rejected,
// reset links expire, provider accounts are linked. It is NOT a security
// boundary. Everything runs in the browser, so the account list (passwords and
// reset tokens included) is readable in devtools and every check here can be
// bypassed by editing client state.
//
// ── Replacing this with a real backend ──────────────────────────────────────
// Keep the exported function signatures and the UI needs no changes.
//
//   signIn()             → POST /auth/login          (server verifies argon2/bcrypt hash)
//   register()           → POST /auth/register
//   signInWithProvider() → OAuth, see PROVIDER NOTES below
//   requestPasswordReset() → POST /auth/forgot       (server emails a signed, single-use link)
//   resetPassword()      → POST /auth/reset          (server validates token + expiry)
//
// ── PROVIDER NOTES ──────────────────────────────────────────────────────────
// `signInWithProvider` is the seam for real OAuth. What each provider needs:
//
//   Google — Google Identity Services. Create an OAuth 2.0 Client ID in Google
//     Cloud Console, add your origin to Authorized JavaScript origins, load
//     https://accounts.google.com/gsi/client, and call google.accounts.id
//     .initialize({ client_id, callback }). The callback receives a JWT
//     credential which the SERVER must verify against Google's JWKS before a
//     session is issued. Never trust it client-side.
//
//   Apple — Sign in with Apple. Requires a paid Apple Developer account, a
//     Services ID, a registered return URL and a .p8 private key. AppleID.auth
//     .signIn() can return an id_token in the browser, but the token exchange
//     is signed with your private key and MUST happen server-side. There is no
//     legitimate client-only Apple flow.
//
// Until those exist, `signInWithProvider` records a provider-linked account
// locally so the rest of the app can be built and tested against it.

export type Provider = "google" | "apple";

/**
 * The public creator identity. Seeded from onboarding and edited from
 * Settings → Edit Profile. Kept separate from the credential fields so a real
 * backend can serve it from a `/me` endpoint without touching auth.
 */
export interface Profile {
  /** Handle shown as @username across the feed, inbox and settings. */
  username: string;
  /** Display name — falls back to "First Last" when never customised. */
  displayName: string;
  bio: string;
  /** Hex used for the generated avatar, picked during onboarding. */
  avatarColor: string;
  privateAccount: boolean;
  location: string;
  website: string;
}

export interface Account {
  firstName: string;
  lastName: string;
  email: string;
  /** Absent for accounts created via a provider that never set one. */
  password?: string;
  /** Providers linked to this account, in addition to any password. */
  providers: Provider[];
  role?: "admin" | "creator" | "user" | "guest";
  /** Absent until onboarding or Edit Profile fills it in. */
  profile?: Profile;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

interface ResetToken {
  token: string;
  email: string;
  expiresAt: number;
  usedAt?: number;
}

const ACCOUNTS_KEY = "connextionz.accounts";
const RESETS_KEY = "connextionz.resets";
const SESSION_KEY = "connextionz.session";
const ACCESS_TOKEN_KEY = "connextionz.accessToken";

/** Matches the "expires in 15 minutes" copy shown on the Reset Sent screen. */
export const RESET_TTL_MS = 15 * 60 * 1000;

export const PROVIDER_LABEL: Record<Provider, string> = { google: "Google", apple: "Apple" };

/** Seed account so the prototype is usable without registering first. */
export const DEMO_ACCOUNT: Account = {
  firstName: "Maya",
  lastName: "Chen",
  email: "demo@connextionz.app",
  password: "collab2026",
  providers: [],
  profile: {
    username: "maya.creates",
    displayName: "Maya Chen",
    bio: "Producer & visual creator. Always down for a studio session 🎧",
    avatarColor: "#00AEEF",
    privateAccount: false,
    location: "Los Angeles, CA",
    website: "connextionz.app/maya",
  },
};

/** A handle derived from the email local part — "maya.chen@x.com" → "maya.chen". */
const handleFromEmail = (email: string) =>
  normalize(email).split("@")[0].replace(/[^a-z0-9._]/g, "") || "creator";

/** Fills in a profile for accounts that predate one (or skipped onboarding). */
export function defaultProfile(account: Account): Profile {
  return {
    username: handleFromEmail(account.email),
    displayName: `${account.firstName} ${account.lastName}`.trim() || handleFromEmail(account.email),
    bio: "",
    avatarColor: "#00AEEF",
    privateAccount: false,
    location: "",
    website: "",
  };
}

/** Always returns a profile, materialising the default when none was stored. */
export const profileOf = (account: Account): Profile => account.profile ?? defaultProfile(account);

const normalize = (email: string) => email.trim().toLowerCase();
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Non-fatal: storage disabled means the session just will not persist. */
  }
}

function loadAccounts(): Account[] {
  const list = read<Account[]>(ACCOUNTS_KEY, []);
  if (!Array.isArray(list)) return [DEMO_ACCOUNT];
  // Normalise older records and guarantee the demo account always exists.
  const accounts = list
    .filter((a): a is Account => !!a && typeof a.email === "string")
    .map((a) => ({ ...a, providers: Array.isArray(a.providers) ? a.providers : [] }));
  return accounts.some((a) => normalize(a.email) === DEMO_ACCOUNT.email)
    ? accounts
    : [DEMO_ACCOUNT, ...accounts];
}

const saveAccounts = (a: Account[]) => write(ACCOUNTS_KEY, a);
const loadResets = () => read<ResetToken[]>(RESETS_KEY, []).filter((t) => !!t && !!t.token);
const saveResets = (t: ResetToken[]) => write(RESETS_KEY, t);

const findAccount = (accounts: Account[], email: string) =>
  accounts.find((a) => normalize(a.email) === normalize(email));

async function tryBackendUpdateProfile(
  patch: Partial<Profile>,
): Promise<Result<Partial<Profile>> | null> {
  try {
    const response = await fetch(`${BACKEND_API_URL}/graphql`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        query: `
          mutation UpdateProfile($input: UpdateProfileInput!) {
            updateProfile(input: $input) {
              username
              displayName
              bio
              avatarColor
              privateAccount
              location
              website
            }
          }
        `,
        variables: { input: patch },
      }),
    });

    if (response.status === 401) return null;

    const json = await response.json().catch(() => null);
    if (!response.ok || !json) {
      return { ok: false, error: "Could not update your profile right now." };
    }

    const errors = Array.isArray(json.errors) ? json.errors : [];
    if (errors.length) {
      return { ok: false, error: errors[0]?.message || "Could not update your profile right now." };
    }

    const backendProfile = json.data?.updateProfile;
    if (!backendProfile) {
      return { ok: false, error: "Could not update your profile right now." };
    }

    return {
      ok: true,
      value: {
        username: backendProfile.username,
        displayName: backendProfile.displayName,
        bio: backendProfile.bio ?? "",
        avatarColor: backendProfile.avatarColor,
        privateAccount: !!backendProfile.privateAccount,
        location: backendProfile.location ?? "",
        website: backendProfile.website ?? "",
      },
    };
  } catch {
    return null;
  }
}

/** Opaque, non-guessable enough for a prototype. A real backend signs these. */
function makeToken(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function roleFromToken(token: string): Account["role"] | undefined {
  try {
    const payload = token.split(".")[1];
    if (!payload) return undefined;
    const decoded = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as { role?: Account["role"] };
    return decoded.role;
  } catch {
    return undefined;
  }
}

// ─── SIGN IN ─────────────────────────────────────────────────────────────────

export async function signIn(email: string, password: string): Promise<Result<Account>> {
  sessionWrite(ACCESS_TOKEN_KEY, null);
  let backendRole: Account["role"] | undefined;
  try {
    const params = new URLSearchParams({ email: email.trim(), password });
    const response = await fetch(`${AUTH_LOGIN_ENDPOINT}?${params.toString()}`, {
      method: "POST",
      credentials: "include",
    });
    if (response.ok) {
      const payload = await response.json() as { access_token?: string };
      if (payload.access_token) {
        sessionWrite(ACCESS_TOKEN_KEY, payload.access_token);
        backendRole = roleFromToken(payload.access_token);
      }
    }
  } catch {
    // The local prototype account remains usable when the backend is offline.
  }

  await delay(900);
  const account = findAccount(loadAccounts(), email);
  if (account && backendRole) account.role = backendRole;

  // An account created purely through a provider has no password to check.
  // Say so explicitly — this is a usability dead end otherwise, and it leaks
  // nothing an attacker could not learn by trying the provider button.
  if (account && !account.password && account.providers.length) {
    const names = account.providers.map((p) => PROVIDER_LABEL[p]).join(" or ");
    return { ok: false, error: `This account uses ${names} sign-in. Continue with ${names} below.` };
  }

  // Identical message for unknown email and wrong password, so the form cannot
  // be used to enumerate which addresses are registered.
  if (!account || account.password !== password) {
    return { ok: false, error: "Incorrect email or password. Please try again." };
  }
  return { ok: true, value: account };
}

export async function register(input: {
  firstName: string; lastName: string; email: string; password: string;
}): Promise<Result<Account>> {
  await delay(1100);
  const accounts = loadAccounts();

  if (findAccount(accounts, input.email)) {
    return { ok: false, error: "An account with this email already exists. Try logging in." };
  }

  const account: Account = { ...input, email: input.email.trim(), providers: [] };
  saveAccounts([...accounts, account]);
  return { ok: true, value: account };
}

// ─── PROVIDER SIGN IN ────────────────────────────────────────────────────────

/**
 * Completes a provider sign-in for an identity the provider has already
 * confirmed. Real OAuth replaces the *caller* (which currently simulates the
 * provider's account chooser) — this linking logic stays as-is.
 *
 * First sign-in creates the account; later ones link the provider to the
 * existing account so a user who registered with a password keeps that access.
 */
export async function signInWithProvider(
  provider: Provider,
  identity: { email: string; firstName: string; lastName: string },
): Promise<Result<Account>> {
  await delay(700);
  const accounts = loadAccounts();
  const existing = findAccount(accounts, identity.email);

  if (existing) {
    if (!existing.providers.includes(provider)) {
      existing.providers = [...existing.providers, provider];
      saveAccounts(accounts);
    }
    return { ok: true, value: existing };
  }

  const account: Account = {
    firstName: identity.firstName,
    lastName: identity.lastName,
    email: identity.email.trim(),
    providers: [provider],
  };
  saveAccounts([...accounts, account]);
  return { ok: true, value: account };
}

// ─── PASSWORD RESET ──────────────────────────────────────────────────────────

/**
 * Always reports success, even for unknown addresses, so the form cannot be
 * used to discover registered emails. `token` comes back only when an account
 * actually exists — a real backend emails it instead of returning it, and the
 * prototype UI surfaces it because no mail is sent.
 */
export async function requestPasswordReset(
  email: string,
): Promise<Result<{ token: string | null }>> {
  await delay(1000);
  const account = findAccount(loadAccounts(), email);
  if (!account) return { ok: true, value: { token: null } };

  const now = Date.now();
  const token: ResetToken = {
    token: makeToken(),
    email: normalize(account.email),
    expiresAt: now + RESET_TTL_MS,
  };

  // Drop this account's earlier tokens so only the newest link works, and
  // discard anything long expired to keep storage from growing forever.
  const kept = loadResets().filter(
    (t) => t.email !== token.email && t.expiresAt > now - RESET_TTL_MS,
  );
  saveResets([...kept, token]);
  return { ok: true, value: { token: token.token } };
}

export function verifyResetToken(token: string): Result<{ email: string }> {
  const entry = loadResets().find((t) => t.token === token.trim());
  if (!entry) return { ok: false, error: "This reset link is not valid. Request a new one." };
  if (entry.usedAt) return { ok: false, error: "This reset link has already been used." };
  if (entry.expiresAt <= Date.now()) return { ok: false, error: "This reset link has expired. Request a new one." };
  return { ok: true, value: { email: entry.email } };
}

export async function resetPassword(token: string, newPassword: string): Promise<Result<Account>> {
  await delay(1000);

  const check = verifyResetToken(token);
  if (!check.ok) return check;

  const accounts = loadAccounts();
  const account = findAccount(accounts, check.value.email);
  if (!account) return { ok: false, error: "That account no longer exists." };

  if (account.password === newPassword) {
    return { ok: false, error: "Choose a password you have not used before." };
  }

  account.password = newPassword;
  saveAccounts(accounts);

  // Single use: burn the token so the same link cannot be replayed.
  const resets = loadResets();
  const entry = resets.find((t) => t.token === token.trim());
  if (entry) { entry.usedAt = Date.now(); saveResets(resets); }

  return { ok: true, value: account };
}

// ─── SESSION ─────────────────────────────────────────────────────────────────
//
// Only the email is persisted; the account is re-read on every access so a
// profile edit in one tab is never served stale from a cached copy. A real
// backend swaps this for an httpOnly session cookie — `getSession()` becomes
// `GET /me` and the rest of the app is unchanged.
//
// Session uses sessionStorage so the user is automatically logged out when
// they close the tab or browser (exit the app).

/** The signed-in account, or null when signed out / the account is gone. */
export function getSession(): Account | null {
  const email = sessionRead<string | null>(SESSION_KEY, null);
  if (!email || typeof email !== "string") return null;
  return findAccount(loadAccounts(), email) ?? null;
}

export function startSession(email: string) {
  sessionWrite(SESSION_KEY, normalize(email));
}

export function endSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  } catch {
    /* Storage disabled — nothing was persisted to clear. */
  }
}

export function getAccessToken(): string | null {
  return sessionRead<string | null>(ACCESS_TOKEN_KEY, null);
}

function sessionRead<T>(key: string, fallback: T): T {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function sessionWrite(key: string, value: unknown) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Non-fatal: storage disabled means the session just will not persist. */
  }
}

// ─── PROFILE ─────────────────────────────────────────────────────────────────

/**
 * Applies a partial profile edit. Username uniqueness is enforced here because
 * handles are how creators address each other across the app.
 */
export async function updateProfile(
  email: string,
  patch: Partial<Profile>,
): Promise<Result<Account>> {
  await delay(700);
  const accounts = loadAccounts();
  const account = findAccount(accounts, email);
  if (!account) return { ok: false, error: "That account no longer exists." };

  const normalizedPatch = {
    ...patch,
    ...normalizeProfilePatch(patch),
  };
  const validationError = getProfileValidationError(normalizedPatch);
  if (validationError) return { ok: false, error: validationError };

  const backend = await tryBackendUpdateProfile(normalizedPatch);
  if (backend) {
    if (!backend.ok) return backend;
    account.profile = { ...profileOf(account), ...backend.value };
    saveAccounts(accounts);
    return { ok: true, value: account };
  }

  const next: Profile = { ...profileOf(account), ...normalizedPatch };
  next.username = next.username.trim().replace(/^@/, "").toLowerCase();

  if (!next.username) return { ok: false, error: "Pick a username." };
  if (!/^[a-z0-9._]{3,24}$/.test(next.username)) {
    return { ok: false, error: "Usernames are 3–24 characters: letters, numbers, dots and underscores." };
  }
  const taken = accounts.some(
    (a) => normalize(a.email) !== normalize(email) && a.profile?.username === next.username,
  );
  if (taken) return { ok: false, error: "That username is already taken." };

  account.profile = next;
  saveAccounts(accounts);
  return { ok: true, value: account };
}

// ─── PASSWORD CHANGE ─────────────────────────────────────────────────────────

/**
 * Changes a password from inside the app, where the user is already
 * authenticated. `current` is still required — it stops someone on an unlocked
 * device from locking the owner out — except on provider-only accounts, which
 * have no password to confirm and are instead *setting* their first one.
 */
export async function changePassword(
  email: string,
  current: string,
  next: string,
): Promise<Result<Account>> {
  await delay(900);
  const accounts = loadAccounts();
  const account = findAccount(accounts, email);
  if (!account) return { ok: false, error: "That account no longer exists." };

  if (account.password !== undefined && account.password !== current) {
    return { ok: false, error: "Your current password is incorrect." };
  }
  if (next.length < 8) return { ok: false, error: "Password must be at least 8 characters." };
  if (account.password === next) {
    return { ok: false, error: "Choose a password you have not used before." };
  }

  account.password = next;
  saveAccounts(accounts);

  // Any outstanding reset link is now stale — a password change should
  // invalidate links mailed before it, exactly as a real backend would.
  saveResets(loadResets().filter((t) => t.email !== normalize(email)));
  return { ok: true, value: account };
}

/** Whether this account is setting a first password rather than changing one. */
export const hasPassword = (account: Account) => account.password !== undefined;

// ─── ACCOUNT DELETION ────────────────────────────────────────────────────────

/**
 * Removes the account and everything keyed to it, then ends the session.
 * Note: `loadAccounts()` re-seeds `DEMO_ACCOUNT`, so deleting the demo login
 * clears its data but keeps the prototype signable-in — deliberate, since
 * otherwise one tester could lock everyone out of the demo.
 */
export async function deleteAccount(email: string): Promise<Result<null>> {
  await delay(1400);

  // If the backend session is active, delete there first so server data
  // (profile, posts, follows) is removed instead of just local draft state.
  try {
    const response = await fetch(GRAPHQL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        query: "mutation DeleteAccount { deleteAccount }",
      }),
    });
    const body = await response.json() as {
      data?: { deleteAccount?: boolean };
      errors?: Array<{ message?: string }>;
    };
    if (response.ok && body.data?.deleteAccount === true && !body.errors?.length) {
      endSession();
      return { ok: true, value: null };
    }
    let message = "The account could not be deleted. Try again.";
    if (body.errors?.[0]?.message) message = body.errors[0].message;
    return { ok: false, error: message };
  } catch {
    // Backend not reachable: continue with local prototype deletion behavior.
  }

  const accounts = loadAccounts();
  const remaining = accounts.filter((a) => normalize(a.email) !== normalize(email));
  saveAccounts(remaining);
  saveResets(loadResets().filter((t) => t.email !== normalize(email)));
  endSession();
  return { ok: true, value: null };
}
