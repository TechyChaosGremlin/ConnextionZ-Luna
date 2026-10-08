import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";

import {
  endSession,
  getAccessToken,
  getRefreshToken,
  getSession,
  changePassword,
  requestPasswordReset,
  register,
  signIn,
  signInWithProvider,
  startSession,
} from "./auth-store.ts";
import { graphqlRequestResult } from "./profile-graphql.ts";

class MemoryStorage {
  private values = new Map<string, string>();

  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, String(value)); }
  removeItem(key: string) { this.values.delete(key); }
}

const originalFetch = globalThis.fetch;

function tokenFor(claims: Record<string, unknown>) {
  const payload = btoa(JSON.stringify(claims))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${payload}.signature`;
}

function setStorage() {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: new MemoryStorage() });
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: new MemoryStorage() });
}

beforeEach(() => {
  setStorage();
});

after(() => {
  globalThis.fetch = originalFetch;
});

test("sign-in authenticates with the backend and loads the authenticated profile", async () => {
  const accessToken = tokenFor({
    sub: "creator-id", email: "creator@example.com", username: "creator", role: "creator",
    exp: Math.floor(Date.now() / 1000) + 900,
  });
  const requests: { url: URL; init?: RequestInit }[] = [];
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input));
    requests.push({ url, init });
    if (url.pathname === "/auth/login") {
      return Response.json({ access_token: accessToken, refresh_token: "refresh-token", token_type: "bearer" });
    }
    return Response.json({ data: { me: {
      username: "creator", displayName: "Creator Name", bio: "Bio", location: "", website: "",
      avatarColor: "#00AEEF", privateAccount: false,
    } } });
  };

  const result = await signIn(" CREATOR@example.com ", "server-password");

  assert.equal(result.ok, true);
  assert.equal(requests[0].url.pathname, "/auth/login");
  assert.equal(requests[0].url.searchParams.get("email"), "creator@example.com");
  assert.equal(requests[0].url.searchParams.get("password"), "server-password");
  assert.equal(requests[0].init?.method, "POST");
  assert.equal(requests[0].init?.body, undefined);
  assert.equal(requests[1].url.pathname, "/graphql");
  assert.equal((requests[1].init?.headers as Record<string, string>).Authorization, `Bearer ${accessToken}`);
  assert.equal(JSON.parse(sessionStorage.getItem("connextionz.accessToken")!), accessToken);
  assert.equal(JSON.parse(sessionStorage.getItem("connextionz.refreshToken")!), "refresh-token");

  assert.equal(getSession()?.profile?.displayName, "Creator Name");
  const storedAccount = JSON.parse(localStorage.getItem("connextionz.accounts")!)[0];
  assert.equal(Object.hasOwn(storedAccount, "password"), false);
});

test("registration uses backend fields and establishes a JWT session", async () => {
  const accessToken = tokenFor({
    sub: "new-id", email: "new.creator@example.com", username: "new.creator", role: "user",
    exp: Math.floor(Date.now() / 1000) + 900,
  });
  const requests: URL[] = [];
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    requests.push(url);
    if (url.pathname === "/auth/register") return Response.json({ user_id: "new-id" }, { status: 201 });
    if (url.pathname === "/auth/login") {
      return Response.json({ access_token: accessToken, refresh_token: "refresh-token", token_type: "bearer" });
    }
    return Response.json({ data: { me: {
      username: "new.creator", displayName: "new.creator", avatarColor: "#00AEEF",
    } } });
  };

  const result = await register({
    firstName: "New", lastName: "Creator", email: "New.Creator@example.com", password: "StrongPass123!",
  });

  assert.equal(result.ok, true);
  assert.equal(requests[0].pathname, "/auth/register");
  assert.equal(requests[0].searchParams.get("email"), "new.creator@example.com");
  assert.equal(requests[0].searchParams.get("username"), "new.creator");
  assert.equal(requests[1].pathname, "/auth/login");
  assert.equal(result.value.firstName, "New");
  assert.equal(result.value.profile?.displayName, "New Creator");
  assert.equal(getAccessToken(), accessToken);
});

test("local credentials cannot authenticate when the backend is unavailable", async () => {
  localStorage.setItem("connextionz.accounts", JSON.stringify([
    { firstName: "Local", lastName: "Only", email: "local@example.com", password: "password", providers: [] },
  ]));
  globalThis.fetch = async () => { throw new Error("offline"); };

  const result = await signIn("local@example.com", "password");

  assert.equal(result.ok, false);
  assert.equal(getAccessToken(), null);
});

test("registration surfaces the FastAPI error envelope without attempting login", async () => {
  const requests: URL[] = [];
  globalThis.fetch = async (input) => {
    requests.push(new URL(String(input)));
    return Response.json({
      error: { code: "CONFLICT", message: "Email already registered" },
    }, { status: 409 });
  };

  const result = await register({
    firstName: "Local", lastName: "Test", email: "local@example.com", password: "StrongPass123!",
  });

  assert.deepEqual(result, { ok: false, error: "Email already registered" });
  assert.deepEqual(requests.map((url) => url.pathname), ["/auth/register"]);
  assert.equal(getAccessToken(), null);
});

test("login surfaces the FastAPI error envelope", async () => {
  globalThis.fetch = async () => Response.json({
    error: { code: "UNAUTHORIZED", message: "Invalid credentials" },
  }, { status: 401 });

  assert.deepEqual(await signIn("local@example.com", "incorrect"), {
    ok: false, error: "Invalid credentials",
  });
});

test("registration preserves legacy FastAPI detail errors", async () => {
  globalThis.fetch = async () => Response.json({
    detail: { message: "Password too weak", errors: ["Use a special character"] },
  }, { status: 400 });

  assert.deepEqual(await register({
    firstName: "Local", lastName: "Test", email: "local@example.com", password: "weak",
  }), { ok: false, error: "Password too weak" });
});

test("legacy local passwords are scrubbed and unsupported auth paths fail closed", async () => {
  localStorage.setItem("connextionz.accounts", JSON.stringify([
    { firstName: "Local", lastName: "Only", email: "local@example.com", password: "plaintext", providers: [] },
  ]));

  assert.equal(getSession(), null);
  const migrated = JSON.parse(localStorage.getItem("connextionz.accounts")!)[0];
  assert.equal(Object.hasOwn(migrated, "password"), false);
  assert.equal(migrated.hasPassword, true);
  assert.equal((await signInWithProvider("google", {
    email: "local@example.com", firstName: "Local", lastName: "Only",
  })).ok, false);
  assert.equal((await requestPasswordReset("local@example.com")).ok, false);
  assert.equal((await changePassword("local@example.com", "old", "new-password")).ok, false);
});

test("GraphQL refreshes an expiring access token and sends the new bearer token", async () => {
  const expiredToken = tokenFor({ exp: Math.floor(Date.now() / 1000) - 1 });
  const freshToken = tokenFor({ exp: Math.floor(Date.now() / 1000) + 900 });
  sessionStorage.setItem("connextionz.accessToken", JSON.stringify(expiredToken));
  sessionStorage.setItem("connextionz.refreshToken", JSON.stringify("refresh-token"));
  const requests: { url: URL; init?: RequestInit }[] = [];
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input));
    requests.push({ url, init });
    if (url.pathname === "/auth/refresh") {
      assert.equal(url.searchParams.get("refresh_token"), "refresh-token");
      return Response.json({ access_token: freshToken, token_type: "bearer", expires_in: 900 });
    }
    return Response.json({ data: { health: "ok" } });
  };

  const result = await graphqlRequestResult<{ health: string }>("query { health }");

  assert.deepEqual(requests.map(({ url }) => url.pathname), ["/auth/refresh", "/graphql"]);
  assert.equal((requests[1].init?.headers as Record<string, string>).Authorization, `Bearer ${freshToken}`);
  assert.equal(getAccessToken(), freshToken);
  assert.deepEqual(result, { ok: true, value: { health: "ok" } });
});

test("logout revokes with the bearer token and clears tab-scoped auth state", async () => {
  const accessToken = tokenFor({ exp: Math.floor(Date.now() / 1000) + 900 });
  sessionStorage.setItem("connextionz.accessToken", JSON.stringify(accessToken));
  sessionStorage.setItem("connextionz.refreshToken", JSON.stringify("refresh-token"));
  startSession("creator@example.com");
  globalThis.fetch = async (input, init) => {
    assert.equal(new URL(String(input)).pathname, "/auth/logout");
    assert.equal((init?.headers as Record<string, string>).Authorization, `Bearer ${accessToken}`);
    return Response.json({ message: "Logged out successfully" });
  };

  await endSession();

  assert.equal(getAccessToken(), null);
  assert.equal(getRefreshToken(), null);
  assert.equal(sessionStorage.getItem("connextionz.session"), null);
});