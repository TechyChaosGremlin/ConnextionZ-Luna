import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { PostMedia } from "./post-media";
import { activatePosts, ownPosts, ownFeedVideos, publishPost, type PostDraft } from "./posts-store";
import { fetchFeedPage } from "./feed-store";
import { fetchMeProfile, fetchMyPosts, resolvePostMediaUrl, uploadPostMediaFile } from "./profile-graphql";
import type { PickedMedia } from "./media-upload";

const POST = "01900000-0000-7000-8000-000000000003";
const MEDIA = "01900000-0000-7000-8000-000000000004";
const THUMB = "01900000-0000-7000-8000-000000000005";
const mediaUrl = `/media/${MEDIA}/content`;
const thumbnail = `/media/${THUMB}/content`;
const poster = "data:image/jpeg;base64,/9j/";
const originalFetch = globalThis.fetch;
const originalWarn = console.warn;
const globals = ["localStorage", "sessionStorage", "navigator", "document", "Image", "HTMLVideoElement"] as const;
const descriptors = globals.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)] as const);
const values = new Map<string, string>();
const draft: PostDraft = {
  caption: "Real upload", hashtags: [], audio: "Original Sound", visibility: "public",
  allowComments: true, allowCollabs: true,
};
let saved: Record<string, unknown>;
let calls: string[];
let failUpload: boolean;
let missingReference: boolean;

class TestVideo {
  videoWidth = 320;
  videoHeight = 240;
  onloadedmetadata?: () => void;
  onseeked?: () => void;
  set src(_value: string) { queueMicrotask(() => this.onloadedmetadata?.()); }
  set currentTime(_value: number) { queueMicrotask(() => this.onseeked?.()); }
}

function picked(kind: "image" | "video"): PickedMedia {
  const type = kind === "image" ? "image/png" : "video/mp4";
  const file = new File([kind === "image" ? new Uint8Array([137, 80, 78, 71]) : new Uint8Array([0, 0, 0, 24])], `post.${kind === "image" ? "png" : "mp4"}`, { type });
  return { file, kind, url: "blob:composer-preview", width: 320, height: 240, durationSec: kind === "video" ? 3 : 0, name: file.name, bytes: file.size, type };
}

function data(value: object) { return Response.json({ data: value }); }

beforeEach(() => {
  values.clear();
  calls = [];
  failUpload = false;
  missingReference = false;
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
  for (const name of ["localStorage", "sessionStorage"]) {
    Object.defineProperty(globalThis, name, { configurable: true, value: storage });
  }
  values.set("connextionz.accessToken", JSON.stringify("media-test-token"));
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: true } });
  Object.defineProperty(globalThis, "HTMLVideoElement", { configurable: true, value: TestVideo });
  Object.defineProperty(globalThis, "Image", { configurable: true, value: class {
    naturalWidth = 320; naturalHeight = 240; onload?: () => void;
    set src(_value: string) { queueMicrotask(() => this.onload?.()); }
  } });
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    createElement: (name: string) => name === "video" ? new TestVideo() : {
      getContext: () => ({ drawImage: () => {} }), toDataURL: () => poster,
    },
  } });
  console.warn = () => {};
  saved = {
    id: POST, thumbnail: "", mediaUrl: null, mediaType: null, caption: draft.caption,
    views: 0, likes: 0, status: "draft",
    creator: { id: "creator", username: "creator", displayName: "Creator" },
  };
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url === poster) return originalFetch(input, init);
    if (url.includes("/media/posts/")) {
      calls.push("upload");
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer media-test-token");
      assert.ok(init?.body instanceof FormData);
      const form = init.body;
      const file = form.get("file");
      assert.ok(file instanceof File);
      assert.equal(file.size > 0, true);
      if (failUpload) return Response.json({ detail: "Media upload failed" }, { status: 503 });
      if (form.get("role") === "thumbnail") {
        saved.thumbnail = thumbnail;
      } else {
        assert.deepEqual(
          new Uint8Array(await file.arrayBuffer()),
          file.type.startsWith("image/") ? new Uint8Array([137, 80, 78, 71]) : new Uint8Array([0, 0, 0, 24]),
        );
        saved.mediaUrl = mediaUrl;
        saved.mediaType = file.type;
        if (file.type.startsWith("image/")) saved.thumbnail = mediaUrl;
      }
      return Response.json(missingReference ? { id: MEDIA } : {
        id: MEDIA, post_id: POST,
        url: form.get("role") === "thumbnail" ? thumbnail : mediaUrl, media_type: file.type,
      }, { status: 201 });
    }
    const request = JSON.parse(String(init?.body));
    assert.match(request.query, /mediaType/);
    if (request.query.includes("mutation CreatePost")) {
      calls.push("create");
      assert.equal(request.variables.input.status, "draft");
      assert.equal(request.variables.input.mediaId, undefined);
      return data({ createPost: saved });
    }
    if (request.query.includes("mutation UpdatePost")) {
      calls.push("publish");
      assert.equal(request.variables.id, POST);
      saved = { ...saved, ...request.variables.input };
      return data({ updatePost: saved });
    }
    if (request.query.includes("query MeProfile")) return data({ me: { id: "creator", username: "creator", posts: [saved] } });
    if (request.query.includes("query MyPosts")) return data({ myPosts: [saved] });
    return data({ feed: { items: [saved], nextCursor: null } });
  };
  activatePosts(null);
  activatePosts("creator@example.test");
});

after(() => {
  activatePosts(null);
  globalThis.fetch = originalFetch;
  console.warn = originalWarn;
  for (const [name, descriptor] of descriptors) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else Reflect.deleteProperty(globalThis, name);
  }
});

for (const kind of ["image", "video"] as const) {
  test(`${kind}: upload associates original bytes and only then publishes the database post ID`, async () => {
    const result = await publishPost(picked(kind), draft);
    assert.equal(result.ok, true);
    if (!result.ok) return;
    assert.equal(result.value.id, POST);
    assert.equal(result.value.mediaUrl, mediaUrl);
    assert.equal(result.value.mediaType, kind === "image" ? "image/png" : "video/mp4");
    assert.deepEqual(calls, kind === "image" ? ["create", "upload", "publish"] : ["create", "upload", "upload", "publish"]);
    assert.equal(ownFeedVideos()[0].mediaUrl, mediaUrl);
    activatePosts(null);
    activatePosts("creator@example.test");
    assert.equal(ownPosts()[0].mediaUrl, mediaUrl);
    assert.doesNotMatch(values.get("connextionz.posts") ?? "", /blob:|data:|Signature/);
    assert.equal((await fetchMeProfile())?.posts?.[0].mediaUrl, mediaUrl);
    assert.equal((await fetchMyPosts())[0].mediaType, result.value.mediaType);
    const feed = await fetchFeedPage(null);
    assert.ok(feed.ok);
    if (feed.ok) assert.equal(feed.value.items[0].mediaType, result.value.mediaType);
  });
}

test("failed uploads do not publish or claim success; retry reuses the same draft ID", async () => {
  const media = picked("image");
  failUpload = true;
  assert.deepEqual(await publishPost(media, draft), { ok: false, error: "Media upload failed" });
  assert.deepEqual(ownPosts(), []);
  assert.deepEqual(calls, ["create", "upload"]);
  failUpload = false;
  assert.equal((await publishPost(media, draft)).ok, true);
  assert.deepEqual(calls, ["create", "upload", "upload", "publish"]);
});

test("missing upload references fail explicitly and never publish", async () => {
  missingReference = true;
  const result = await publishPost(picked("image"), draft);
  assert.equal(result.ok, false);
  assert.deepEqual(ownPosts(), []);
  assert.equal(calls.includes("publish"), false);
});

test("a failed video thumbnail upload never publishes an incomplete video", async () => {
  const send = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    if (init?.body instanceof FormData && init.body.get("role") === "thumbnail") {
      return Response.json({ detail: "Thumbnail upload failed" }, { status: 503 });
    }
    return send(input, init);
  };
  assert.deepEqual(await publishPost(picked("video"), draft), { ok: false, error: "Thumbnail upload failed" });
  assert.equal(calls.includes("publish"), false);
  assert.deepEqual(ownPosts(), []);
});

test("missing media in the completed mutation is an error, never a local-only success", async () => {
  const send = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    if (String(init?.body).includes("mutation UpdatePost")) {
      return data({ updatePost: { ...saved, mediaUrl: null } });
    }
    return send(input, init);
  };
  const result = await publishPost(picked("image"), draft);
  assert.equal(result.ok, false);
  assert.deepEqual(ownPosts(), []);
});

test("failed post creation never uploads files or invents a local post ID", async () => {
  globalThis.fetch = async () => Response.json({ errors: [{ message: "Post creation unavailable" }] });
  assert.deepEqual(await publishPost(picked("image"), draft), { ok: false, error: "Post creation unavailable" });
  assert.deepEqual(ownPosts(), []);
});

test("cancelled upload is not published", async () => {
  const controller = new AbortController();
  controller.abort();
  assert.deepEqual(await publishPost(picked("image"), draft, { signal: controller.signal }), { ok: false, error: "cancelled" });
  assert.deepEqual(calls, []);
});

test("protected media resolves with bearer auth, and resolves again after URL expiration/reload", async () => {
  let requests = 0;
  globalThis.fetch = async (input, init) => {
    assert.ok(String(input).endsWith(`/media/${MEDIA}/url`));
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer media-test-token");
    assert.equal(init?.cache, "no-store");
    requests++;
    return Response.json({ url: `https://storage.example.test/object?signature=${requests}` });
  };
  assert.deepEqual(await resolvePostMediaUrl(mediaUrl), { ok: true, value: "https://storage.example.test/object?signature=1" });
  assert.deepEqual(await resolvePostMediaUrl(mediaUrl), { ok: true, value: "https://storage.example.test/object?signature=2" });
  assert.equal(requests, 2);
});

test("media resolution and upload errors remain explicit", async () => {
  assert.equal((await resolvePostMediaUrl("")).ok, false);
  globalThis.fetch = async () => Response.json({ detail: "Not allowed to view media" }, { status: 403 });
  assert.deepEqual(await resolvePostMediaUrl(mediaUrl), { ok: false, error: "Not allowed to view media" });
  globalThis.fetch = async () => Response.json({});
  assert.equal((await resolvePostMediaUrl(mediaUrl)).ok, false);
  assert.equal((await uploadPostMediaFile(picked("image").file, "post.png", POST)).ok, false);
});

test("media requests refresh an expired session and retry with the refreshed bearer token", async () => {
  values.set("connextionz.refreshToken", JSON.stringify("test-refresh-token"));
  let mediaRequests = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).endsWith("/auth/refresh")) {
      return Response.json({ access_token: "refreshed-media-token", token_type: "bearer" });
    }
    mediaRequests++;
    if (mediaRequests === 1) return Response.json({ detail: "Expired session" }, { status: 401 });
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer refreshed-media-token");
    return Response.json({ url: "https://storage.example.test/refreshed" });
  };
  assert.equal((await resolvePostMediaUrl(mediaUrl)).ok, true);
  assert.equal(mediaRequests, 2);
});

test("media without an authenticated session never makes an anonymous storage request", async () => {
  values.delete("connextionz.accessToken");
  globalThis.fetch = async () => { throw new Error("An anonymous request must not be sent"); };
  assert.deepEqual(await resolvePostMediaUrl(mediaUrl), { ok: false, error: "Sign in again to access post media." });
});

test("renderer distinguishes image and video metadata and reports missing media", () => {
  const image = renderToStaticMarkup(<PostMedia src="https://storage.example.test/photo" mediaType="image/png" alt="Post image" />);
  assert.match(image, /<img/);
  assert.doesNotMatch(image, /<video/);
  const video = renderToStaticMarkup(<PostMedia src="https://storage.example.test/movie" mediaType="video/mp4" alt="Post video" controls />);
  assert.match(video, /<video/);
  assert.match(video, /controls=""/);
  const missing = renderToStaticMarkup(<PostMedia src="" alt="Missing post" />);
  assert.match(missing, /role="alert"/);
  assert.match(missing, /no media reference/);
  const protectedImage = renderToStaticMarkup(<PostMedia src={mediaUrl} mediaType="image/png" alt="Private object" />);
  assert.doesNotMatch(protectedImage, /<img[^>]+src="\/media\//);
});
