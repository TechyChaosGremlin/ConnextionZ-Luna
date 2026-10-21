import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { type Account } from "./auth-store";
import { fetchMeProfile } from "./profile-graphql";
import { SettingsScreen } from "./Settings";

const account: Account = {
  email: "navigation@example.test", firstName: "Cached", lastName: "Name", providers: [],
  profile: {
    username: "cached.name", displayName: "Cached Name", avatarColor: "#00AEEF",
    bio: "", website: "", location: "", privateAccount: false,
  },
};
const noop = () => {};
const originalFetch = globalThis.fetch;
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");

after(() => {
  globalThis.fetch = originalFetch;
  if (originalStorage) Object.defineProperty(globalThis, "sessionStorage", originalStorage);
  else Reflect.deleteProperty(globalThis, "sessionStorage");
});

test("Profile navigation renders the owner profile, not Settings, inside the authenticated session", async () => {
  const app = await readFile("src/app/App.tsx", "utf8");
  assert.match(app, /id: "profile", icon: User, label: "Profile"/);
  assert.match(app, /id === "profile"\) setScreen\("profile"\)/);
  const profileRoute = app.match(/screen === "profile" && \(([\s\S]*?)<\/AnimatePresence>/)?.[1];
  assert.ok(profileRoute);
  assert.match(profileRoute, /<OwnProfileScreen key=\{account.email\}/);
  assert.doesNotMatch(profileRoute, /<SettingsScreen/);
  assert.match(profileRoute, /onOpenSettings=\{\(\) => \{ setSettingsRoute\(null\); setScreen\("settings"\); \}\}/);
  assert.match(profileRoute, /onEditProfile=\{\(\) => \{ setSettingsRoute\("editProfile"\); setScreen\("settings"\); \}\}/);
  assert.match(app, /<SessionProvider key=\{account.email\} account=\{account\} onAccountChange=\{setAccount\}>/);
});

test("Settings has its own destination, preserves edit subroutes, and returns to Profile", async () => {
  const app = await readFile("src/app/App.tsx", "utf8");
  const settingsRoute = app.match(/screen === "settings" && \(([\s\S]*?)<\/AnimatePresence>/)?.[1];
  assert.ok(settingsRoute);
  assert.match(settingsRoute, /<SettingsScreen/);
  assert.match(settingsRoute, /initialRoute=\{settingsRoute\}/);
  assert.match(settingsRoute, /onBack=\{\(\) => setScreen\("profile"\)\}/);
  assert.match(settingsRoute, /onOpenProfile=\{\(\) => setScreen\("profile"\)\}/);
  assert.doesNotMatch(settingsRoute, /<OwnProfileScreen/);
  const settings = await readFile("src/app/Settings.tsx", "utf8");
  assert.match(settings, /<button onClick=\{onOpenProfile\} aria-label="View your creator profile"/);
  assert.match(settings, /route: "editProfile", label: "Edit Profile"/);
});

test("Settings root and Edit Profile are distinct rendered destinations", () => {
  const props = {
    account, onBack: noop, onOpenProfile: noop, onLogout: noop, onDeleteProfile: noop,
    onAccountChange: noop,
  };
  const root = renderToStaticMarkup(<SettingsScreen {...props} />);
  assert.match(root, /<h1[^>]*>Settings<\/h1>/);
  assert.match(root, /aria-label="View your creator profile"/);
  assert.doesNotMatch(root, /How other creators see you|312 collabs/);
  const edit = renderToStaticMarkup(<SettingsScreen {...props} initialRoute="editProfile" />);
  assert.match(edit, /How other creators see you/);
  assert.match(edit, /Save Changes/);
});

test("owner profile loads the authenticated backend UUID and username, not cached or demo identities", async () => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: { getItem: (key: string) => key === "connextionz.accessToken" ? JSON.stringify("navigation-test-token") : null },
  });
  const profile = {
    id: "01900000-0000-7000-8000-000000000099",
    username: "backend.owner", displayName: "Backend Owner", posts: [], playlists: [],
    following: 2, followers: 9,
  };
  globalThis.fetch = async (_input, init) => {
    const { query, variables } = JSON.parse(String(init?.body));
    assert.match(query, /query MeProfile\s*\{\s*me\s*\{/);
    assert.match(query, /id\s+username/);
    assert.doesNotMatch(query, /profile\(username:|cached.name|nova.dj/);
    assert.equal(variables, undefined);
    assert.equal((init?.headers as Record<string, string>).Authorization, "Bearer navigation-test-token");
    return Response.json({ data: { me: profile } });
  };
  assert.deepEqual(await fetchMeProfile(), profile);
  const source = await readFile("src/app/Profile.tsx", "utf8");
  const ownerHook = source.slice(source.indexOf("export function useOwnCreator"), source.indexOf("// ─── SCREEN"));
  assert.match(ownerHook, /id: profile.id/);
  assert.match(ownerHook, /username: profile.username/);
  assert.match(ownerHook, /posts: \(profile.posts \?\? \[\]\)/);
  assert.match(ownerHook, /playlists: \(profile.playlists \?\? \[\]\)/);
  assert.doesNotMatch(ownerHook, /OWN_STATS|OWN_POSTS|OWN_PLAYLISTS|useOwnContent|id: "me"|creatorByUsername/);
  assert.match(ownerHook, /if \(!active\) return/);
  assert.match(ownerHook, /state.email === account.email/);
  assert.match(ownerHook, /role="alert"/);
  assert.match(ownerHook, />Retry<\/button>/);
});

test("missing authenticated profile is not replaced with a live demo profile", async () => {
  globalThis.fetch = async () => Response.json({ data: { me: null } });
  assert.equal(await fetchMeProfile(), null);
  const source = await readFile("src/app/Profile.tsx", "utf8");
  assert.match(source, /if \(!profile\?\.id \|\| !profile.username\)/);
  assert.match(source, /Could not load your creator profile/);
  assert.doesNotMatch(source, /updateAvatar/);
  assert.match(source, /<button onClick=\{onEditProfile\} aria-label="Edit profile"/);
});
