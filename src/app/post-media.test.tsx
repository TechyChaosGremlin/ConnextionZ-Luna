import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { PostMedia } from "./post-media";
import { PostMediaRepair } from "./PostMediaRepair";
import { canRepairPost, repairPostMedia, validateRepairFile, type RepairProgress } from "./post-media-repair";
import type { GraphQLPost } from "./profile-graphql";
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
const globals = ["localStorage", "sessionStorage", "navigator", "document", "Image", "HTMLVideoElement", "crypto"] as const;
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
let receipts: Map<string, { id: string; url: string; media_type: string }>;

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
  receipts = new Map();
  let identities = 0;
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: {
    randomUUID: () => identities++ === 0 ? MEDIA : THUMB,
  } });
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
    if (url.includes(`/media/posts/${POST}/uploads/`)) {
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer media-test-token");
      const parsed = new URL(url, "http://localhost");
      const id = parsed.pathname.split("/").at(-1)!;
      const receipt = receipts.get(id);
      if (!receipt) return Response.json({ attachment: null });
      const reference = parsed.searchParams.get("role") === "primary" ? saved.mediaUrl : saved.thumbnail;
      if (receipt.url !== reference || (parsed.searchParams.has("expected_primary_id") &&
          saved.mediaUrl !== `/media/${parsed.searchParams.get("expected_primary_id")}/content`)) {
        return Response.json({ detail: "This upload is no longer attached." }, { status: 409 });
      }
      return Response.json(receipt);
    }
    if (url.includes("/media/posts/")) {
      calls.push("upload");
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer media-test-token");
      assert.ok(init?.body instanceof FormData);
      const form = init.body;
      const file = form.get("file");
      assert.ok(file instanceof File);
      assert.equal(file.size > 0, true);
      if (failUpload) return Response.json({ detail: "Media upload failed" }, { status: 503 });
      if ((form.get("role") === "primary" && saved.mediaUrl) ||
          (form.get("role") === "thumbnail" && saved.thumbnail)) {
        return Response.json({ detail: "Media already exists." }, { status: 409 });
      }
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
      const attached = {
        id: String(form.get("upload_id") ?? (form.get("role") === "thumbnail" ? THUMB : MEDIA)),
        url: form.get("role") === "thumbnail" ? thumbnail : mediaUrl, media_type: file.type,
      };
      if (form.get("upload_id")) receipts.set(attached.id, attached);
      return Response.json(missingReference ? { id: MEDIA } : {
        ...attached, post_id: POST,
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
      assert.equal(request.variables.input.mediaId, MEDIA);
      assert.equal(request.variables.input.thumbnailMediaId, saved.mediaType === "video/mp4" ? THUMB : MEDIA);
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

function brokenPost(): GraphQLPost {
  return {
    id: POST, caption: "Keep this caption", mediaUrl: null, thumbnail: "", mediaType: "video",
    likes: 17, comments: 6, shares: 4, saves: 3, views: 100, visibility: "private",
    status: "published", allowComments: false, allowCollabs: false,
  };
}

function jpeg() { return new File([new Uint8Array([255, 216, 255])], "poster.jpg", { type: "image/jpeg" }); }

test("repair attaches both files to the existing published UUID without metadata mutations", async () => {
  const post = brokenPost();
  saved = { ...post };
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    if (String(input).includes("/media/posts/")) {
      assert.ok(String(input).endsWith(`/media/posts/${POST}`));
      assert.ok(init?.body instanceof FormData);
      assert.equal(init.body.get("repair_missing"), "true");
      assert.equal(init.body.get("role"), calls.length === 0 ? "primary" : "thumbnail");
      assert.equal(init.body.get("expected_primary_id"), calls.length === 0 ? null : MEDIA);
    }
    return previousFetch(input, init);
  };
  const result = await repairPostMedia(post, picked("video").file, jpeg(), {});
  assert.ok(result.ok);
  assert.deepEqual(calls, ["upload", "upload"]);
  for (const [key, value] of Object.entries(post)) {
    if (!["mediaUrl", "thumbnail", "mediaType"].includes(key)) assert.deepEqual(saved[key], value);
  }
});

test("repair resumes only the JPEG after a partial failure", async () => {
  const post = brokenPost();
  saved = { ...post };
  const progress: RepairProgress = {};
  const previousFetch = globalThis.fetch;
  let rejectThumbnail = true;
  globalThis.fetch = async (input, init) => {
    if (init?.body instanceof FormData && init.body.get("role") === "thumbnail") {
      assert.equal(init.body.get("expected_primary_id"), MEDIA);
    }
    if (init?.body instanceof FormData && init.body.get("role") === "thumbnail" && rejectThumbnail) {
      return Response.json({ detail: "Storage unavailable" }, { status: 503 });
    }
    return previousFetch(input, init);
  };
  const first = await repairPostMedia(post, picked("video").file, jpeg(), progress);
  assert.ok(!first.ok && first.error.includes("Video is attached"));
  assert.equal(progress.primaryUrl, mediaUrl);
  rejectThumbnail = false;
  assert.ok((await repairPostMedia(post, picked("video").file, jpeg(), progress)).ok);
  assert.deepEqual(calls, ["upload", "upload"]);
});

test("thumbnail repair rejects a stale expected video without retrying a primary upload", async () => {
  const post = { ...brokenPost(), mediaUrl, mediaType: "video/mp4" };
  saved = { ...post };
  const send = globalThis.fetch;
  let attempts = 0;
  globalThis.fetch = async (input, init) => {
    if (init?.body instanceof FormData) {
      assert.equal(init.body.get("role"), "thumbnail");
      assert.equal(init.body.get("expected_primary_id"), MEDIA);
      attempts++;
      return Response.json({ detail: "The confirmed video no longer matches this post." }, { status: 409 });
    }
    return send(input, init);
  };
  const result = await repairPostMedia(post, null, jpeg(), {});
  assert.ok(!result.ok && result.error.includes("confirmed video no longer matches"));
  assert.equal(attempts, 1);
  assert.deepEqual(calls, []);
  assert.equal(saved.thumbnail, "");
});

test("a lost repair upload response cannot resend or replace the committed video", async () => {
  const post = brokenPost();
  saved = { ...post };
  const send = globalThis.fetch;
  const progress: RepairProgress = {};
  globalThis.fetch = async (input, init) => {
    const result = await send(input, init);
    if (init?.body instanceof FormData && init.body.get("role") === "primary") {
      throw new TypeError("Simulated lost response");
    }
    return result;
  };
  assert.equal((await repairPostMedia(post, picked("video").file, jpeg(), progress)).ok, false);
  assert.equal(saved.mediaUrl, mediaUrl);
  const retry = await repairPostMedia(post, picked("video").file, jpeg(), progress);
  assert.ok(!retry.ok && retry.error.includes("upload may have completed"));
  assert.deepEqual(calls, ["upload"]);
});

test("repair rejects unavailable ownership verification and surfaces authorization errors", async () => {
  const post = brokenPost();
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => data({ me: { posts: [] } });
  assert.equal((await repairPostMedia(post, picked("video").file, jpeg(), {})).ok, false);
  assert.deepEqual(calls, []);
  saved = { ...post };
  globalThis.fetch = async (input, init) => String(input).includes("/media/posts/")
    ? Response.json({ detail: "Not allowed to upload media" }, { status: 403 })
    : previousFetch(input, init);
  const denied = await repairPostMedia(post, picked("video").file, jpeg(), {});
  assert.ok(!denied.ok && denied.error.includes("Not allowed"));
  assert.equal(saved.thumbnail, "");
});

test("repair does not resend a video after a lost response or overwrite existing media", async () => {
  const post = brokenPost();
  saved = { ...post, mediaUrl, mediaType: "video/mp4" };
  const result = await repairPostMedia(post, picked("video").file, jpeg(), {});
  assert.ok(!result.ok && result.error.includes("upload may have completed"));
  assert.deepEqual(calls, []);
  assert.equal(canRepairPost({ ...post, thumbnail }), false);
  assert.equal(canRepairPost({ ...post, mediaUrl, mediaType: "image/png" }), false);
});

test("repair verifies completed uploads again without uploading or publishing", async () => {
  const post = brokenPost();
  saved = { ...post };
  const progress: RepairProgress = {};
  assert.ok((await repairPostMedia(post, picked("video").file, jpeg(), progress)).ok);
  assert.ok((await repairPostMedia(post, null, jpeg(), progress)).ok);
  assert.deepEqual(calls, ["upload", "upload"]);
});

test("repair validates selections and requires explicit confirmation in its UI", () => {
  assert.equal(validateRepairFile(jpeg(), "primary").ok, false);
  assert.equal(validateRepairFile(picked("image").file, "thumbnail").ok, false);
  assert.equal(validateRepairFile(new File([], "empty.jpg", { type: "image/jpeg" }), "thumbnail").ok, false);
  const html = renderToStaticMarkup(<PostMediaRepair posts={[brokenPost()]} onClose={() => {}} />);
  assert.match(html, /role="dialog"/);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /Confirm and attach media/);
  assert.match(html, /type="submit" disabled/);
  assert.match(html, new RegExp(POST));
  assert.match(html, /not a restoration/);
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

test("composer retry reuses confirmed video and thumbnail attachments", async () => {
  const media = picked("video");
  const send = globalThis.fetch;
  let rejectThumbnail = true;
  let rejectPublish = true;
  const roles: string[] = [];
  globalThis.fetch = async (input, init) => {
    if (init?.body instanceof FormData) {
      const role = String(init.body.get("role"));
      roles.push(role);
      if (role === "thumbnail" && rejectThumbnail) {
        return Response.json({ detail: "Thumbnail upload failed" }, { status: 503 });
      }
    } else if (String(init?.body).includes("mutation UpdatePost") && rejectPublish) {
      return Response.json({ errors: [{ message: "Publish failed" }] });
    }
    return send(input, init);
  };
  assert.equal((await publishPost(media, draft)).ok, false);
  rejectThumbnail = false;
  assert.equal((await publishPost(media, draft)).ok, false);
  rejectPublish = false;
  assert.equal((await publishPost(media, draft)).ok, true);
  assert.deepEqual(roles, ["primary", "thumbnail", "thumbnail"]);
  assert.equal(calls.filter((item) => item === "create").length, 1);
  assert.equal(saved.id, POST);
});

for (const lostRole of ["primary", "thumbnail"] as const) {
  test(`composer reconciles a committed ${lostRole} after a lost response and publishes the same draft`, async () => {
    const media = picked("video");
    const send = globalThis.fetch;
    let loseResponse = true;
    globalThis.fetch = async (input, init) => {
      const response = await send(input, init);
      if (init?.body instanceof FormData && init.body.get("role") === lostRole && loseResponse) {
        assert.equal(init.body.get("upload_id"), lostRole === "primary" ? MEDIA : THUMB);
        loseResponse = false;
        throw new TypeError("Simulated response loss after attachment");
      }
      return response;
    };
    assert.equal((await publishPost(media, draft)).ok, false);
    assert.equal(calls.includes("publish"), false);
    const recovered = await publishPost(media, draft);
    assert.ok(recovered.ok);
    assert.equal(recovered.value.id, POST);
    assert.deepEqual(calls, ["create", "upload", "upload", "publish"]);
    assert.equal(receipts.size, 2);
  });
}

test("composer cannot adopt another attachment identity or publish after failed reconciliation", async () => {
  const send = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    if (String(input).includes("/uploads/")) {
      return Response.json({ id: THUMB, url: thumbnail, media_type: "video/mp4" });
    }
    return send(input, init);
  };
  assert.equal((await publishPost(picked("video"), draft)).ok, false);
  assert.deepEqual(calls, ["create"]);
});

test("composer stops if reconciliation is unauthorized instead of uploading or publishing", async () => {
  const send = globalThis.fetch;
  globalThis.fetch = async (input, init) => String(input).includes("/uploads/")
    ? Response.json({ detail: "Not allowed to verify this upload" }, { status: 403 })
    : send(input, init);
  assert.equal((await publishPost(picked("video"), draft)).ok, false);
  assert.deepEqual(calls, ["create"]);
});

test("composer publish sends confirmed identities and rejects a changed slot", async () => {
  const send = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    if (String(init?.body).includes("mutation UpdatePost")) {
      const request = JSON.parse(String(init?.body));
      assert.equal(request.variables.input.mediaId, MEDIA);
      assert.equal(request.variables.input.thumbnailMediaId, THUMB);
      return Response.json({ errors: [{ message: "Post media changed. Reload before publishing." }] });
    }
    return send(input, init);
  };
  const result = await publishPost(picked("video"), draft);
  assert.ok(!result.ok && result.error.includes("media changed"));
  assert.equal(saved.status, "draft");
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
