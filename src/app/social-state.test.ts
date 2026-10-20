import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { fetchFeedPage } from "./feed-store";
import { registerCreator } from "./creators";
import { activateLikeGraph, getLikeState, likeStateRevision, noteLikeState, toggleLike } from "./like-store";
import {
  activateFollowGraph, followStateRevision, isFollowing, noteFollowState, toggleFollow,
} from "./follow-store";
import {
  addComment, fetchComments, fetchMeProfile, fetchProfileByUsername,
  savePost, sharePost, trackPostWatch,
} from "./profile-graphql";
import { loadMoreVideos } from "./search.ts";

const POST = "01900000-0000-7000-8000-000000000003";
const CREATOR = "01900000-0000-7000-8000-000000000004";
const originalFetch = globalThis.fetch;
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
const originalWarn = console.warn;
const creator = { id: CREATOR, username: "nova.dj", displayName: "Nova", followers: 8, isFollowing: true };
const post = {
  id: POST, thumbnail: "", caption: "Test", views: 1, likes: 4, isLiked: true,
  isSaved: true, isShared: true, creator,
};

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((settle) => { resolve = settle; });
  return { promise, resolve };
}

function data(value: object) { return Response.json({ data: value }); }
function body(init?: RequestInit): { query: string; variables: Record<string, unknown> } {
  return JSON.parse(String(init?.body));
}

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    },
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: true } });
  console.warn = () => {};
  globalThis.fetch = async () => data({ myFollowing: [] });
  activateLikeGraph(null);
  activateFollowGraph(null);
  activateLikeGraph("viewer@example.test");
  activateFollowGraph("viewer@example.test");
});

after(() => {
  globalThis.fetch = originalFetch;
  console.warn = originalWarn;
  for (const [key, descriptor] of [["sessionStorage", originalStorage], ["navigator", originalNavigator]] as const) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else Reflect.deleteProperty(globalThis, key);
  }
});

test("failed feed is an error, never numeric demo posts; empty feed stays empty", async () => {
  globalThis.fetch = async () => Response.json({ errors: [{ message: "Unavailable" }] });
  assert.equal((await fetchFeedPage(null)).ok, false);
  globalThis.fetch = async () => data({ feed: { items: [], nextCursor: null } });
  assert.deepEqual(await fetchFeedPage(null), { ok: true, value: { items: [], cursor: null } });
});

test("feed preserves database UUID through comments, watch, save and share", async () => {
  const calls: string[] = [];
  globalThis.fetch = async (_input, init) => {
    const request = body(init);
    if (request.query.includes("query Feed")) {
      assert.match(request.query, /isLiked/);
      assert.match(request.query, /isFollowing/);
      return data({ feed: { items: [post], nextCursor: null } });
    }
    const id = request.variables.postId ?? request.variables.id;
    assert.equal(id, POST);
    assert.match(request.query, /UUID!/);
    calls.push(request.query);
    if (request.query.includes("query Comments")) return data({ comments: [] });
    if (request.query.includes("mutation AddComment")) return data({ addComment: { id: POST, text: "Hello" } });
    if (request.query.includes("mutation TrackPostWatch")) return data({ trackPostWatch: { views: 2 } });
    if (request.query.includes("mutation SavePost")) return data({ savePost: { saved: true, saves: 1 } });
    return data({ sharePost: { shared: true, shares: 1 } });
  };
  const page = await fetchFeedPage(null);
  assert.equal(page.ok, true);
  if (!page.ok) return;
  assert.equal(page.value.items[0].id, POST);
  assert.equal(page.value.items[0].creatorId, CREATOR);
  assert.equal(isFollowing(CREATOR), true);
  assert.equal(getLikeState(POST).liked, true);
  assert.equal((await fetchComments(page.value.items[0].id)).ok, true);
  assert.equal((await addComment(page.value.items[0].id, "Hello")).ok, true);
  assert.equal((await trackPostWatch(page.value.items[0].id, 5, true)).ok, true);
  assert.equal((await savePost(page.value.items[0].id)).ok, true);
  assert.equal((await sharePost(page.value.items[0].id)).ok, true);
  assert.equal(calls.length, 5);
});

test("API creator never inherits demo identity or demo posts by username", () => {
  const registered = registerCreator(creator);
  assert.equal(registered.id, CREATOR);
  assert.deepEqual(registered.posts, []);
  assert.equal(registered.followers, 7);
});

test("likes apply mutation truth, dedupe clicks, reject stale reads, accept fresh refetches", async () => {
  noteLikeState(POST, false, 4);
  const oldRead = likeStateRevision();
  const response = deferred();
  let requests = 0;
  globalThis.fetch = async (_input, init) => {
    requests++;
    assert.equal(body(init).variables.id, POST);
    assert.match(body(init).query, /LikePost/);
    return response.promise;
  };
  const first = toggleLike(POST);
  assert.deepEqual(getLikeState(POST), { liked: true, likes: 5, pending: true });
  await toggleLike(POST);
  noteLikeState(POST, false, 4, oldRead);
  assert.equal(getLikeState(POST).liked, true);
  assert.equal(requests, 1);
  response.resolve(data({ likePost: { liked: true, likes: 12 } }));
  assert.deepEqual(await first, { ok: true, value: true });
  noteLikeState(POST, false, 4, oldRead);
  assert.deepEqual(getLikeState(POST), { liked: true, likes: 12, pending: false });
  noteLikeState(POST, false, 11);
  assert.deepEqual(getLikeState(POST), { liked: false, likes: 11, pending: false });
  globalThis.fetch = async () => data({ likePost: { liked: false, likes: 11 } });
  await toggleLike(POST);
  assert.equal(getLikeState(POST).liked, false);
});

test("failed like rolls back count/state and reports auth failure instead of local success", async () => {
  noteLikeState(POST, true, 4);
  globalThis.fetch = async (_input, init) => {
    assert.match(body(init).query, /UnlikePost/);
    return Response.json({ errors: [{ message: "Authentication required" }] });
  };
  assert.deepEqual(await toggleLike(POST), { ok: false, error: "Authentication required" });
  assert.deepEqual(getLikeState(POST), { liked: true, likes: 4, pending: false });
});

test("follow uses returned state and username, dedupes and protects completed writes from old snapshots", async () => {
  registerCreator(creator);
  noteFollowState(CREATOR, false);
  const oldRead = followStateRevision();
  const response = deferred();
  let requests = 0;
  globalThis.fetch = async (_input, init) => {
    requests++;
    assert.equal(body(init).variables.username, "nova.dj");
    return response.promise;
  };
  const first = toggleFollow(CREATOR);
  await toggleFollow(CREATOR);
  assert.equal(requests, 1);
  noteFollowState(CREATOR, false, oldRead);
  assert.equal(isFollowing(CREATOR), true);
  response.resolve(data({ follow: { following: false, followers: 7, followingCount: 0 } }));
  assert.deepEqual(await first, { ok: true, value: false });
  assert.equal(isFollowing(CREATOR), false);
  noteFollowState(CREATOR, true, oldRead);
  assert.equal(isFollowing(CREATOR), false);
  noteFollowState(CREATOR, true);
  assert.equal(isFollowing(CREATOR), true);
});

test("failed follow and unfollow roll back and surface errors without a token", async () => {
  registerCreator(creator);
  globalThis.fetch = async () => Response.json({ errors: [{ message: "Authentication required" }] });
  assert.deepEqual(await toggleFollow(CREATOR), { ok: false, error: "Authentication required" });
  assert.equal(isFollowing(CREATOR), false);
  noteFollowState(CREATOR, true);
  assert.equal((await toggleFollow(CREATOR)).ok, false);
  assert.equal(isFollowing(CREATOR), true);
});

test("late sign-in following snapshot cannot undo a completed unfollow", async () => {
  const initial = deferred();
  globalThis.fetch = async (_input, init) => body(init).query.includes("query MyFollowing")
    ? initial.promise : data({ unfollow: { following: false, followers: 7, followingCount: 0 } });
  activateFollowGraph("another@example.test");
  noteFollowState(CREATOR, true);
  await toggleFollow(CREATOR);
  initial.resolve(data({ myFollowing: [creator] }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(isFollowing(CREATOR), false);
});

test("responses from previous accounts cannot change new-account state, even after switching back", async () => {
  registerCreator(creator);
  const likeResponse = deferred();
  const followResponse = deferred();
  globalThis.fetch = async (_input, init) => {
    const { query } = body(init);
    if (query.includes("query MyFollowing")) return data({ myFollowing: [] });
    return query.includes("mutation LikePost") ? likeResponse.promise : followResponse.promise;
  };
  const liking = toggleLike(POST);
  const following = toggleFollow(CREATOR);
  activateLikeGraph(null);
  activateFollowGraph(null);
  activateLikeGraph("viewer@example.test");
  activateFollowGraph("viewer@example.test");
  likeResponse.resolve(data({ likePost: { liked: true, likes: 99 } }));
  followResponse.resolve(data({ follow: { following: true, followers: 99, followingCount: 1 } }));
  await Promise.all([liking, following]);
  assert.equal(getLikeState(POST).liked, undefined);
  assert.equal(isFollowing(CREATOR), false);
});

test("refreshed feed, search, and profile queries carry persisted viewer state", async () => {
  globalThis.fetch = async (_input, init) => {
    const { query } = body(init);
    if (query.includes("query Feed")) return data({ feed: { items: [post], nextCursor: null } });
    if (query.includes("query SearchPosts")) {
      assert.match(query, /isFollowing/);
      return data({ searchPosts: { items: [post], nextCursor: null } });
    }
    assert.match(query, /isFollowing/);
    assert.match(query, /posts\s*\{[^}]*isLiked/);
    return data(query.includes("query MeProfile") ? { me: { ...creator, posts: [post] } } : { profile: { ...creator, posts: [post] } });
  };
  activateLikeGraph(null);
  activateLikeGraph("viewer@example.test");
  await fetchFeedPage(null);
  assert.equal(getLikeState(POST).liked, true);
  const results = await loadMoreVideos("Test", null);
  assert.equal(results.ok && results.value.videos[0].isLiked, true);
  assert.equal((await fetchMeProfile())?.posts?.[0].isLiked, true);
  assert.equal((await fetchProfileByUsername("nova.dj"))?.isFollowing, true);
});

test("like/follow and unlike/unfollow survive navigation, refetch and a viewer-store reload", async () => {
  let liked = false;
  let followed = false;
  let mutations = 0;
  globalThis.fetch = async (_input, init) => {
    const { query, variables } = body(init);
    const profile = () => ({ ...creator, isFollowing: followed, followers: followed ? 8 : 7 });
    if (query.includes("query MyFollowing")) return data({ myFollowing: followed ? [profile()] : [] });
    if (query.includes("query Feed")) {
      return data({ feed: { items: [{ ...post, isLiked: liked, likes: liked ? 5 : 4, creator: profile() }], nextCursor: null } });
    }
    mutations++;
    if (query.includes("mutation LikePost") || query.includes("mutation UnlikePost")) {
      assert.equal(variables.id, POST);
      liked = query.includes("mutation LikePost");
      return data(liked ? { likePost: { liked, likes: 5 } } : { unlikePost: { liked, likes: 4 } });
    }
    assert.equal(variables.username, "nova.dj");
    followed = query.includes("mutation Follow");
    const result = { following: followed, followers: followed ? 8 : 7, followingCount: followed ? 1 : 0 };
    return data(followed ? { follow: result } : { unfollow: result });
  };
  await fetchFeedPage(null);
  await toggleLike(POST);
  await toggleFollow(CREATOR);
  await fetchFeedPage(null);
  assert.deepEqual(getLikeState(POST), { liked: true, likes: 5, pending: false });
  assert.equal(isFollowing(CREATOR), true);

  activateLikeGraph(null);
  activateFollowGraph(null);
  activateLikeGraph("viewer@example.test");
  activateFollowGraph("viewer@example.test");
  await fetchFeedPage(null);
  assert.deepEqual(getLikeState(POST), { liked: true, likes: 5, pending: false });
  assert.equal(isFollowing(CREATOR), true);
  await toggleLike(POST);
  await toggleFollow(CREATOR);
  await fetchFeedPage(null);
  assert.deepEqual(getLikeState(POST), { liked: false, likes: 4, pending: false });
  assert.equal(isFollowing(CREATOR), false);
  assert.equal(mutations, 4);
});

test("a delayed feed snapshot cannot overwrite a newer like or follow result", async () => {
  registerCreator(creator);
  noteLikeState(POST, false, 4);
  const oldFeed = deferred();
  globalThis.fetch = async (_input, init) => {
    const { query } = body(init);
    if (query.includes("query Feed")) return oldFeed.promise;
    return query.includes("mutation LikePost")
      ? data({ likePost: { liked: true, likes: 5 } })
      : data({ follow: { following: true, followers: 8, followingCount: 1 } });
  };
  const loading = fetchFeedPage(null);
  await Promise.all([toggleLike(POST), toggleFollow(CREATOR)]);
  oldFeed.resolve(data({ feed: { items: [{ ...post, isLiked: false, creator: { ...creator, isFollowing: false } }], nextCursor: null } }));
  await loading;
  assert.deepEqual(getLikeState(POST), { liked: true, likes: 5, pending: false });
  assert.equal(isFollowing(CREATOR), true);
});
