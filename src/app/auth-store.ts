import {
  AUTH_LOGIN_ENDPOINT,
  AUTH_LOGOUT_ENDPOINT,
  AUTH_REFRESH_ENDPOINT,
  AUTH_REGISTER_ENDPOINT,
  GRAPHQL_ENDPOINT,
} from "./api-config.ts";
import { getProfileValidationError, normalizeProfilePatch } from "./profile-validation.ts";

// ─── ACCOUNT STORE ───────────────────────────────────────────────────────────
// The backend verifies credentials and issues JWTs. Browser storage holds only
// non-secret account metadata and tab-scoped access/refresh tokens.

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
  /** Retained only for migrating legacy local records; never written back. */
  password?: string;
  hasPassword?: boolean;
  /** Providers linked to this account, in addition to any password. */
  providers: Provider[];
  role?: "admin" | "creator" | "user" | "guest";
  /** Absent until onboarding or Edit Profile fills it in. */
  profile?: Profile;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

const ACCOUNTS_KEY = "connextionz.accounts";
const SESSION_KEY = "connextionz.session";
const ACCESS_TOKEN_KEY = "connextionz.accessToken";
const REFRESH_TOKEN_KEY = "connextionz.refreshToken";

export const PROVIDER_LABEL: Record<Provider, string> = { google: "Google", apple: "Apple" };

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
  if (!Array.isArray(list)) return [];
  const accounts = list
    .filter((a): a is Account => !!a && typeof a.email === "string")
    .map(({ password, ...account }) => ({
      ...account,
      hasPassword: account.hasPassword ?? password !== undefined,
      providers: Array.isArray(account.providers) ? account.providers : [],
    }));
  if (list.some((account) => account?.password !== undefined)) saveAccounts(accounts);
  return accounts;
}

const saveAccounts = (accounts: Account[]) => write(
  ACCOUNTS_KEY,
  accounts.map(({ password: _password, ...account }) => account),
);

const findAccount = (accounts: Account[], email: string) =>
  accounts.find((a) => normalize(a.email) === normalize(email));

function saveAccount(account: Account) {
  const accounts = loadAccounts();
  const index = accounts.findIndex((item) => normalize(item.email) === normalize(account.email));
  if (index === -1) accounts.push(account);
  else accounts[index] = account;
  saveAccounts(accounts);
}

async function tryBackendUpdateProfile(
  patch: Partial<Profile>,
): Promise<Result<Partial<Profile>>> {
  try {
    const accessToken = getAccessToken();
    const response = await fetch(GRAPHQL_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
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

    if (response.status === 401) {
      return { ok: false, error: "Your session has expired. Sign in again to continue." };
    }

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
    return { ok: false, error: "Could not reach the server. Check your connection and try again." };
  }
}

function tokenClaims(token: string): {
  sub?: string; email?: string; username?: string; role?: Account["role"]; exp?: number;
} | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const decoded = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
    return JSON.parse(decoded) as {
      sub?: string; email?: string; username?: string; role?: Account["role"]; exp?: number;
    };
  } catch {
    return null;
  }
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null) as {
    detail?: string | { message?: string; errors?: string[] };
    error?: { message?: string };
  } | null;
  if (body?.error?.message) return body.error.message;
  if (typeof body?.detail === "string") return body.detail;
  if (body?.detail?.message) return body.detail.message;
  if (body?.detail?.errors?.length) return body.detail.errors.join(" ");
  return fallback;
}

// ─── SIGN IN ─────────────────────────────────────────────────────────────────

export async function signIn(email: string, password: string): Promise<Result<Account>> {
  clearAccessTokens();
  try {
    const response = await fetch(AUTH_LOGIN_ENDPOINT, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: normalize(email), password }),
    });
    if (!response.ok) {
      return { ok: false, error: await responseError(response, "Incorrect email or password.") };
    }
    const tokens = await response.json() as {
      access_token?: string; refresh_token?: string; token_type?: string;
    };
    if (!tokens.access_token || !tokens.refresh_token || tokens.token_type?.toLowerCase() !== "bearer") {
      return { ok: false, error: "The server returned an invalid sign-in response." };
    }

    const claims = tokenClaims(tokens.access_token);
    if (!claims?.sub || !claims.email || !claims.username || !claims.role) {
      return { ok: false, error: "The server returned an invalid access token." };
    }
    sessionWrite(ACCESS_TOKEN_KEY, tokens.access_token);
    sessionWrite(REFRESH_TOKEN_KEY, tokens.refresh_token);

    const profileResponse = await fetch(GRAPHQL_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${tokens.access_token}`,
      },
      credentials: "include",
      body: JSON.stringify({
        query: `query CurrentAccount {
          me { username displayName avatarUrl avatarColor bio location website privateAccount }
        }`,
      }),
    });
    if (!profileResponse.ok) {
      clearAccessTokens();
      return { ok: false, error: await responseError(profileResponse, "Could not load your account.") };
    }
    const profilePayload = await profileResponse.json() as {
      data?: { me?: {
        username: string; displayName: string; avatarColor?: string | null;
        bio?: string | null; location?: string | null; website?: string | null; privateAccount?: boolean;
      } | null };
      errors?: { message?: string }[];
    };
    const backendProfile = profilePayload.data?.me;
    if (profilePayload.errors?.length || !backendProfile) {
      clearAccessTokens();
      return {
        ok: false,
        error: profilePayload.errors?.[0]?.message || "Could not load your account.",
      };
    }

    const displayName = backendProfile.displayName || backendProfile.username;
    const [firstName = backendProfile.username, ...lastNames] = displayName.trim().split(/\s+/);
    const account: Account = {
      firstName,
      lastName: lastNames.join(" "),
      email: normalize(claims.email),
      providers: [],
      role: claims.role,
      hasPassword: true,
      profile: {
        username: backendProfile.username,
        displayName,
        bio: backendProfile.bio ?? "",
        avatarColor: backendProfile.avatarColor ?? "#00AEEF",
        privateAccount: !!backendProfile.privateAccount,
        location: backendProfile.location ?? "",
        website: backendProfile.website ?? "",
      },
    };
    saveAccount(account);
    startSession(account.email);
    return { ok: true, value: account };
  } catch {
    clearAccessTokens();
    return { ok: false, error: "Could not reach the authentication server. Try again." };
  }
}

export async function register(input: {
  firstName: string; lastName: string; email: string; password: string;
}): Promise<Result<Account>> {
  const email = normalize(input.email);
  const username = handleFromEmail(email).slice(0, 24) || "creator";
  try {
    const response = await fetch(AUTH_REGISTER_ENDPOINT, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, username, password: input.password }),
    });
    if (!response.ok) {
      return { ok: false, error: await responseError(response, "Could not create your account.") };
    }

    const signedIn = await signIn(email, input.password);
    if (!signedIn.ok) return signedIn;

    const displayName = `${input.firstName.trim()} ${input.lastName.trim()}`.trim();
    const account: Account = {
      ...signedIn.value,
      firstName: input.firstName.trim(),
      lastName: input.lastName.trim(),
      profile: {
        ...profileOf(signedIn.value),
        displayName: displayName || profileOf(signedIn.value).displayName,
      },
    };
    saveAccount(account);
    return { ok: true, value: account };
  } catch {
    return { ok: false, error: "Could not reach the authentication server. Try again." };
  }
}

// ─── PROVIDER SIGN IN ────────────────────────────────────────────────────────

/** Provider OAuth is unavailable until a backend provider-verification flow exists. */
export async function signInWithProvider(
  provider: Provider,
  _identity: { email: string; firstName: string; lastName: string },
): Promise<Result<Account>> {
  return { ok: false, error: `${PROVIDER_LABEL[provider]} sign-in is not configured yet.` };
}

// ─── PASSWORD RESET ──────────────────────────────────────────────────────────

/** Backend password-reset delivery and confirmation are not implemented yet. */
export async function requestPasswordReset(
  _email: string,
): Promise<Result<{ token: string | null }>> {
  return { ok: false, error: "Password reset is not available yet." };
}

export function verifyResetToken(_token: string): Result<{ email: string }> {
  return { ok: false, error: "Password reset is not available yet." };
}

export async function resetPassword(
  _token: string,
  _newPassword: string,
): Promise<Result<Account>> {
  return { ok: false, error: "Password reset is not available yet." };
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
  const accounts = loadAccounts();
  const email = sessionRead<string | null>(SESSION_KEY, null);
  if (!email || typeof email !== "string") return null;
  if (!getAccessToken()) return null;
  return findAccount(accounts, email) ?? null;
}

export function startSession(email: string) {
  sessionWrite(SESSION_KEY, normalize(email));
}

export async function endSession(): Promise<void> {
  const accessToken = getAccessToken();
  try {
    sessionStorage.removeItem(SESSION_KEY);
    clearAccessTokens();
  } catch {
    // Continue with remote revocation even if browser storage is unavailable.
  }
  try {
    if (accessToken) {
      await fetch(AUTH_LOGOUT_ENDPOINT, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        credentials: "include",
      });
    }
  } catch {
    // Always clear this tab's credentials, even when the backend is unreachable.
  } finally {
    try {
      sessionStorage.removeItem(SESSION_KEY);
      clearAccessTokens();
    } catch {
      // The current UI still transitions to the logged-out state.
    }
  }
}

export function getAccessToken(): string | null {
  return sessionRead<string | null>(ACCESS_TOKEN_KEY, null);
}

export function getRefreshToken(): string | null {
  return sessionRead<string | null>(REFRESH_TOKEN_KEY, null);
}

function clearAccessTokens() {
  try {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    // Storage may be disabled; authentication will fail closed on the next request.
  }
}

export function accessTokenNeedsRefresh(token = getAccessToken()): boolean {
  if (!token) return false;
  const exp = tokenClaims(token)?.exp;
  return typeof exp === "number" && exp * 1000 <= Date.now() + 30_000;
}

export async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  try {
    const response = await fetch(AUTH_REFRESH_ENDPOINT, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) {
      clearAccessTokens();
      return null;
    }
    const payload = await response.json() as { access_token?: string; token_type?: string };
    if (!payload.access_token || payload.token_type?.toLowerCase() !== "bearer") {
      clearAccessTokens();
      return null;
    }
    sessionWrite(ACCESS_TOKEN_KEY, payload.access_token);
    return payload.access_token;
  } catch {
    return null;
  }
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
  if (!backend.ok) return backend;
  account.profile = { ...profileOf(account), ...backend.value };
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
  _email: string,
  _current: string,
  _next: string,
): Promise<Result<Account>> {
  return { ok: false, error: "Password changes are not available yet." };
}

/** Whether this account is setting a first password rather than changing one. */
export const hasPassword = (account: Account) => account.hasPassword ?? account.password !== undefined;

// ─── ACCOUNT DELETION ────────────────────────────────────────────────────────

/** Remove the backend account and clear this browser's cached identity. */
export async function deleteAccount(email: string): Promise<Result<null>> {
  const accessToken = getAccessToken();
  if (!accessToken) return { ok: false, error: "Sign in again before deleting your account." };
  try {
    const response = await fetch(GRAPHQL_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
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
      saveAccounts(loadAccounts().filter((account) => normalize(account.email) !== normalize(email)));
      await endSession();
      return { ok: true, value: null };
    }
    let message = "The account could not be deleted. Try again.";
    if (body.errors?.[0]?.message) message = body.errors[0].message;
    return { ok: false, error: message };
  } catch {
    return { ok: false, error: "Could not reach the server. Try again." };
  }
}
