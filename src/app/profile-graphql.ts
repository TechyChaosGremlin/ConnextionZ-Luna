export type GraphQLPost = {
  id: string;
  thumbnail: string;
  mediaUrl?: string | null;
  caption: string;
  views: number;
  likes: number;
  isLiked?: boolean | null;
  hashtags?: string[] | null;
  audio?: string | null;
  visibility?: string | null;
  allowComments?: boolean | null;
  allowCollabs?: boolean | null;
  durationSec?: number | null;
  comments?: number | null;
  shares?: number | null;
  saves?: number | null;
  isSaved?: boolean | null;
  isShared?: boolean | null;
  collabWith?: string | null;
  status?: string | null;
  scheduledAt?: string | null;
};

export type GraphQLNotification = {
  id: string;
  type: string;
  actorId: string | null;
  title: string;
  body: string | null;
  isRead: boolean;
  createdAt: string;
};

export type UploadedPostMedia = {
  id: string;
  url: string;
  contentType: string;
};

export type GraphQLPlaylist = {
  id: string;
  title: string;
  cover: string;
  itemLabel: string;
  plays: number;
};

export type GraphQLProfileSummary = {
  id: string;
  username: string;
  displayName: string;
  avatarUrl?: string | null;
  avatarColor?: string | null;
  verified?: boolean | null;
  collabScore?: number | null;
  collabCount?: number | null;
  followers?: number | null;
  following?: number | null;
  openToCollab?: boolean | null;
  isFollowing?: boolean | null;
};

export type GraphQLFeedItem = GraphQLPost & {
  creator: GraphQLProfileSummary;
};

export type GraphQLComment = {
  id: string;
  text: string;
  likes: number;
  isLiked: boolean;
  canDelete: boolean;
  canEdit: boolean;
  moderationStatus: string;
  createdAt: string;
  author: GraphQLProfileSummary;
};

export type GraphQLHashtagResult = {
  tag: string;
  posts: number;
  views: number;
};

export type GraphQLSound = {
  id: string;
  title: string;
  creator: string;
  creatorAvatar: string;
  artwork: string;
  genre: string;
  videoCount: number;
  totalPlays: number;
  rank: number;
  growthPct: number;
  duration: string;
  bpm: number;
};

export type GraphQLProfile = {
  id: string;
  username: string;
  displayName: string;
  avatarUrl?: string | null;
  avatarColor?: string | null;
  bio?: string | null;
  location?: string | null;
  website?: string | null;
  verified?: boolean | null;
  online?: boolean | null;
  collabStatus?: string | null;
  collabScore?: number | null;
  collabCount?: number | null;
  followers?: number | null;
  following?: number | null;
  openToCollab?: boolean | null;
  responseTime?: string | null;
  isFollowing?: boolean | null;
  posts?: GraphQLPost[] | null;
  playlists?: GraphQLPlaylist[] | null;
};

import { BACKEND_API_URL, GRAPHQL_ENDPOINT } from "./api-config";
import { getAccessToken, type Result } from "./auth-store";


async function uploadFile(file: Blob, filename: string, kind: "media" | "avatar"): Promise<Record<string, unknown> | null> {
  try {
    const form = new FormData();
    form.append("file", file, filename);
    const endpoint = kind === "avatar" ? `${BACKEND_API_URL}/api/avatar/upload` : `${BACKEND_API_URL}/api/media/upload`;
    const response = await fetch(endpoint, {
      method: "POST",
      body: form,
      credentials: "include",
    });
    if (!response.ok) return null;
    return await response.json() as Record<string, unknown>;
  } catch (error) {
    console.warn("Media upload unavailable", error);
    return null;
  }
}


export async function fetchNotificationsFromApi(): Promise<Result<GraphQLNotification[]>> {
  const result = await graphqlRequestResult<{
    notifications: { edges: { node: GraphQLNotification }[] };
  }>(`
    query Notifications {
      notifications {
        edges {
          node {
            id
            type
            actorId
            title
            body
            isRead
            createdAt
          }
        }
      }
    }
  `);

  if (!result.ok) {
    return result;
  }

  return {
    ok: true,
    value: result.value.notifications.edges.map((edge) => edge.node),
  };
}

export async function markNotificationReadFromApi(
  id: string,
): Promise<Result<boolean>> {
  const result = await graphqlRequestResult<{
    markNotificationRead: boolean;
  }>(
    `
      mutation MarkNotificationRead($id: UUID!) {
        markNotificationRead(id: $id)
      }
    `,
    { id },
  );

  if (!result.ok) {
    return result;
  }

  return {
    ok: true,
    value: result.value.markNotificationRead,
  };
}

export async function markAllNotificationsReadFromApi(): Promise<Result<boolean>> {
  const result = await graphqlRequestResult<{
    markAllNotificationsRead: boolean;
  }>(`
    mutation MarkAllNotificationsRead {
      markAllNotificationsRead
    }
  `);

  if (!result.ok) {
    return result;
  }

  return {
    ok: true,
    value: result.value.markAllNotificationsRead,
  };
}

export async function uploadMediaFile(file: Blob, filename: string, kind: "media" | "avatar" = "media"): Promise<string | null> {
  const data = await uploadFile(file, filename, kind);
  return typeof data?.url === "string" ? new URL(data.url, BACKEND_API_URL).toString() : null;
}

export async function uploadPostMediaFile(file: Blob, filename: string, postId: string): Promise<UploadedPostMedia | null> {
  try {
    const form = new FormData();
    form.append("file", file, filename);
    const accessToken = getAccessToken();
    const response = await fetch(`${BACKEND_API_URL}/media/posts/${postId}`, {
      method: "POST",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      body: form,
      credentials: "include",
    });
    if (!response.ok) return null;
    const data = await response.json() as Record<string, unknown>;
    const contentType = data.contentType ?? data.media_type;
    if (typeof data.id !== "string" || typeof data.url !== "string" || typeof contentType !== "string") {
      return null;
    }
    return {
      id: data.id,
      url: new URL(data.url, BACKEND_API_URL).toString(),
      contentType,
    };
  } catch (error) {
    console.warn("Post media upload unavailable", error);
    return null;
  }
}

export async function fetchFeedPageFromApi(cursor: string | null, limit = 10, following = false): Promise<{
  items: GraphQLFeedItem[];
  nextCursor: string | null;
} | null> {
  const data = await graphqlRequest<{ feed: { items: GraphQLFeedItem[]; nextCursor: string | null } }>(`
    query Feed($cursor: String, $limit: Int!, $following: Boolean!) {
      feed(cursor: $cursor, limit: $limit, following: $following) {
        nextCursor
        items {
          id
          thumbnail
          mediaUrl
          caption
          views
          likes
          isLiked
          hashtags
          audio
          visibility
          allowComments
          allowCollabs
          durationSec
          comments
          shares
          saves
          isSaved
          isShared
          collabWith
          creator { id username displayName avatarUrl avatarColor verified collabScore collabCount followers following openToCollab }
        }
      }
    }
  `, { cursor, limit, following });
  return data?.feed ?? null;
}

export type GraphQLWatchResult = {
  views: number;
  watchedSeconds: number;
  completed: boolean;
  rewatched: boolean;
};

export async function trackPostWatch(
  postId: string,
  watchedSeconds: number,
  completed: boolean,
): Promise<Result<GraphQLWatchResult>> {
  return graphqlRequestResult<{ trackPostWatch: GraphQLWatchResult }>(`
    mutation TrackPostWatch($postId: UUID!, $watchedSeconds: Float!, $completed: Boolean!) {
      trackPostWatch(postId: $postId, watchedSeconds: $watchedSeconds, completed: $completed) {
        views watchedSeconds completed rewatched
      }
    }
  `, { postId, watchedSeconds, completed }).then((result) => (
    result.ok ? { ok: true, value: result.value.trackPostWatch } : result
  ));
}

export async function fetchComments(postId: string, limit = 100): Promise<Result<GraphQLComment[]>> {
  const result = await graphqlRequestResult<{ comments: GraphQLComment[] }>(`
    query Comments($postId: UUID!, $limit: Int!) {
      comments(postId: $postId, limit: $limit) {
        id text likes isLiked canDelete canEdit moderationStatus createdAt
        author { id username displayName avatarUrl avatarColor }
      }
    }
  `, { postId, limit });
  return result.ok ? { ok: true, value: result.value.comments } : result;
}

export async function addComment(postId: string, text: string): Promise<Result<GraphQLComment>> {
  const result = await graphqlRequestResult<{ addComment: GraphQLComment }>(`
    mutation AddComment($postId: UUID!, $text: String!) {
      addComment(postId: $postId, text: $text) {
        id text likes isLiked canDelete canEdit moderationStatus createdAt
        author { id username displayName avatarUrl avatarColor }
      }
    }
  `, { postId, text });
  return result.ok ? { ok: true, value: result.value.addComment } : result;
}

export async function editComment(id: string, text: string): Promise<Result<GraphQLComment>> {
  const result = await graphqlRequestResult<{ editComment: GraphQLComment }>(`
    mutation EditComment($id: UUID!, $text: String!) {
      editComment(id: $id, text: $text) {
        id text likes isLiked canDelete canEdit moderationStatus createdAt
        author { id username displayName avatarUrl avatarColor }
      }
    }
  `, { id, text });
  return result.ok ? { ok: true, value: result.value.editComment } : result;
}

export async function reportComment(id: string, reason: string): Promise<Result<boolean>> {
  const result = await graphqlRequestResult<{ reportComment: boolean }>(`
    mutation ReportComment($id: UUID!, $reason: String!) { reportComment(id: $id, reason: $reason) }
  `, { id, reason });
  return result.ok ? { ok: true, value: result.value.reportComment } : result;
}

export async function deleteComment(id: string): Promise<Result<boolean>> {
  const result = await graphqlRequestResult<{ deleteComment: boolean }>(`
    mutation DeleteComment($id: UUID!) { deleteComment(id: $id) }
  `, { id });
  return result.ok ? { ok: true, value: result.value.deleteComment } : result;
}

export type GraphQLCommentLikeResult = { liked: boolean; likes: number };

export async function likeComment(id: string): Promise<Result<GraphQLCommentLikeResult>> {
  const result = await graphqlRequestResult<{ likeComment: GraphQLCommentLikeResult }>(`
    mutation LikeComment($id: UUID!) { likeComment(id: $id) { liked likes } }
  `, { id });
  return result.ok ? { ok: true, value: result.value.likeComment } : result;
}

export async function unlikeComment(id: string): Promise<Result<GraphQLCommentLikeResult>> {
  const result = await graphqlRequestResult<{ unlikeComment: GraphQLCommentLikeResult }>(`
    mutation UnlikeComment($id: UUID!) { unlikeComment(id: $id) { liked likes } }
  `, { id });
  return result.ok ? { ok: true, value: result.value.unlikeComment } : result;
}

export type GraphQLFeedPage = {
  items: GraphQLFeedItem[];
  nextCursor: string | null;
};

export type GraphQLHashtagPage = {
  hashtags: GraphQLHashtagResult[];
  nextCursor: string | null;
};

export type SearchSortBy = "relevance" | "recent" | "popular";

export type SearchPostFilters = {
  hashtag?: string;
  sortBy?: SearchSortBy;
};

export type SearchProfileFilters = {
  verifiedOnly?: boolean;
  openToCollab?: boolean;
};

export async function searchPosts(
  query: string, after: string | null = null, limit = 20, filters: SearchPostFilters = {},
): Promise<Result<GraphQLFeedPage>> {
  const result = await graphqlRequestResult<{ searchPosts: GraphQLFeedPage }>(`
    query SearchPosts($query: String!, $after: String, $limit: Int!, $hashtag: String, $sortBy: String!) {
      searchPosts(query: $query, after: $after, limit: $limit, hashtag: $hashtag, sortBy: $sortBy) {
        nextCursor
        items {
          id thumbnail mediaUrl caption views likes isLiked hashtags audio visibility
          allowComments allowCollabs durationSec comments shares saves isSaved isShared collabWith
          creator { id username displayName avatarUrl avatarColor verified }
        }
      }
    }
  `, { query, after, limit, hashtag: filters.hashtag ?? null, sortBy: filters.sortBy ?? "relevance" });
  return result.ok ? { ok: true, value: result.value.searchPosts } : result;
}

export async function searchHashtags(query: string, after: string | null = null, limit = 20): Promise<Result<GraphQLHashtagPage>> {
  const result = await graphqlRequestResult<{ searchHashtags: GraphQLHashtagPage }>(`
    query SearchHashtags($query: String!, $after: String, $limit: Int!) {
      searchHashtags(query: $query, after: $after, limit: $limit) {
        nextCursor
        hashtags { tag posts views }
      }
    }
  `, { query, after, limit });
  return result.ok ? { ok: true, value: result.value.searchHashtags } : result;
}

export type GraphQLSearchSuggestion = { type: "creator" | "hashtag" | "query"; value: string; label: string };

export async function fetchSearchSuggestions(prefix: string, limit = 8): Promise<GraphQLSearchSuggestion[] | null> {
  const data = await graphqlRequest<{ searchSuggestions: GraphQLSearchSuggestion[] }>(`
    query SearchSuggestions($prefix: String!, $limit: Int!) {
      searchSuggestions(prefix: $prefix, limit: $limit) { type value label }
    }
  `, { prefix, limit });
  return data?.searchSuggestions ?? null;
}

export type GraphQLSearchHistoryEntry = { query: string; createdAt: string };

export async function fetchSearchHistory(limit = 10): Promise<GraphQLSearchHistoryEntry[] | null> {
  const data = await graphqlRequest<{ searchHistory: GraphQLSearchHistoryEntry[] }>(`
    query SearchHistory($limit: Int!) {
      searchHistory(limit: $limit) { query createdAt }
    }
  `, { limit });
  return data?.searchHistory ?? null;
}

export async function fetchTrendingSearches(limit = 8): Promise<string[] | null> {
  const data = await graphqlRequest<{ trendingSearches: string[] }>(`
    query TrendingSearches($limit: Int!) {
      trendingSearches(limit: $limit)
    }
  `, { limit });
  return data?.trendingSearches ?? null;
}

export async function recordSearch(query: string): Promise<boolean> {
  const data = await graphqlRequest<{ recordSearch: boolean }>(`
    mutation RecordSearch($query: String!) { recordSearch(query: $query) }
  `, { query });
  return data?.recordSearch ?? false;
}

export async function clearSearchHistoryRemote(): Promise<boolean> {
  const data = await graphqlRequest<{ clearSearchHistory: boolean }>(`
    mutation ClearSearchHistory { clearSearchHistory }
  `);
  return data?.clearSearchHistory ?? false;
}

export async function removeSearchHistoryEntryRemote(query: string): Promise<boolean> {
  const data = await graphqlRequest<{ removeSearchHistoryEntry: boolean }>(`
    mutation RemoveSearchHistoryEntry($query: String!) { removeSearchHistoryEntry(query: $query) }
  `, { query });
  return data?.removeSearchHistoryEntry ?? false;
}


export async function fetchTrendingSounds(genre?: string): Promise<GraphQLSound[] | null> {
  const data = await graphqlRequest<{ trendingSounds: GraphQLSound[] }>(`
    query TrendingSounds($genre: String) {
      trendingSounds(genre: $genre) {
        id title creator creatorAvatar artwork genre videoCount totalPlays
        rank growthPct duration bpm
      }
    }
  `, { genre });
  return data?.trendingSounds ?? null;
}

/**
 * Runs a GraphQL request and reports *why* it failed — a dropped connection,
 * an HTTP error, or a GraphQL error — instead of collapsing every case into
 * `null`. Callers that need to tell "no more results" apart from "the request
 * failed" (pagination, in particular) should use this over `graphqlRequest`.
 */
export async function graphqlRequestResult<T>(query: string, variables?: Record<string, unknown>): Promise<Result<T>> {
  let res: Response;
  try {
    const accessToken = getAccessToken();
    res = await fetch(GRAPHQL_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      credentials: "include",  // Include cookies for session auth
      body: JSON.stringify({ query, variables }),
    });
  } catch (error) {
    console.warn("GraphQL unavailable", error);
    return { ok: false, error: "Could not reach the server. Check your connection and try again." };
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    console.warn("GraphQL request failed", res.status, body);
    return {
      ok: false,
      error: res.status === 401
        ? "Your session has expired. Sign in again to continue."
        : res.status >= 500
          ? "The server ran into a problem. Try again in a moment."
          : "That request could not be completed.",
    };
  }

  let json: { data?: T; errors?: { message?: string }[] };
  try {
    json = await res.json();
  } catch (error) {
    console.warn("GraphQL response was not valid JSON", error);
    return { ok: false, error: "The server sent back something unexpected." };
  }

  if (json.errors?.length) {
    console.warn("GraphQL errors", json.errors);
    return { ok: false, error: json.errors[0]?.message || "That request could not be completed." };
  }

  return { ok: true, value: json.data as T };
}

async function graphqlRequest<T>(query: string, variables?: Record<string, unknown>): Promise<T | null> {
  const result = await graphqlRequestResult<T>(query, variables);
  return result.ok ? result.value : null;
}

export async function fetchMeProfile(): Promise<GraphQLProfile | null> {
  const data = await graphqlRequest<{ me: GraphQLProfile | null }>(`
    query MeProfile {
      me {
        id
        username
        displayName
        avatarUrl
        avatarColor
        bio
        location
        website
        verified
        online
        collabStatus
        collabScore
        collabCount
        followers
        following
        openToCollab
        responseTime
        isFollowing
        posts {
          id
          thumbnail
          mediaUrl
          caption
          views
          likes
          hashtags
          audio
          visibility
          allowComments
          allowCollabs
          durationSec
          comments
          shares
          saves
          collabWith
        }
        playlists {
          id
          title
          cover
          itemLabel
          plays
        }
      }
    }
  `);

  return data?.me ?? null;
}

export async function fetchProfileByUsername(username: string): Promise<GraphQLProfile | null> {
  const data = await graphqlRequest<{ profile: GraphQLProfile | null }>(`
    query ProfileByUsername($username: String!) {
      profile(username: $username) {
        id
        username
        displayName
        avatarUrl
        avatarColor
        bio
        location
        website
        verified
        online
        collabStatus
        collabScore
        collabCount
        followers
        following
        openToCollab
        responseTime
        isFollowing
        posts {
          id
          thumbnail
          mediaUrl
          caption
          views
          likes
          hashtags
          audio
          visibility
          allowComments
          allowCollabs
          durationSec
          comments
          shares
          saves
          collabWith
        }
        playlists {
          id
          title
          cover
          itemLabel
          plays
        }
      }
    }
  `, { username });

  return data?.profile ?? null;
}

export async function searchProfiles(
  query: string, after: string | null = null, limit = 20, filters: SearchProfileFilters = {},
): Promise<Result<GraphQLProfilePage>> {
  const result = await graphqlRequestResult<{ searchProfiles: GraphQLProfilePage }>(`
    query SearchProfiles($query: String!, $after: String, $limit: Int!, $verifiedOnly: Boolean!, $openToCollab: Boolean) {
      searchProfiles(query: $query, after: $after, limit: $limit, verifiedOnly: $verifiedOnly, openToCollab: $openToCollab) {
        nextCursor
        profiles { ${profileSummaryFields} }
      }
    }
  `, { query, after, limit, verifiedOnly: filters.verifiedOnly ?? false, openToCollab: filters.openToCollab ?? null });
  return result.ok ? { ok: true, value: result.value.searchProfiles } : result;
}

export type UpdateProfilePatch = {
  username?: string;
  displayName?: string;
  bio?: string;
  location?: string;
  website?: string;
  avatarUrl?: string;
  avatarColor?: string;
  collabStatus?: string;
  openToCollab?: boolean;
};

export async function updateMeProfile(patch: UpdateProfilePatch): Promise<GraphQLProfile | null> {
  const data = await graphqlRequest<{ updateProfile: GraphQLProfile | null }>(`
    mutation UpdateProfile($input: UpdateProfileInput!) {
      updateProfile(input: $input) {
        id
        username
        displayName
        avatarUrl
        avatarColor
        bio
        location
        website
        avatarColor
        collabStatus
        openToCollab
      }
    }
  `, { input: patch });

  return data?.updateProfile ?? null;
}

const postFields = `
  id
  thumbnail
  mediaUrl
  caption
  views
  likes
  isLiked
  hashtags
  audio
  visibility
  allowComments
  allowCollabs
  durationSec
  comments
  shares
  saves
  isSaved
  isShared
  collabWith
  status
  scheduledAt
`;

const playlistFields = `
  id
  title
  cover
  itemLabel
  plays
`;

export async function fetchMyPosts(): Promise<GraphQLPost[]> {
  const data = await graphqlRequest<{ myPosts: GraphQLPost[] }>(`
    query MyPosts { myPosts { ${postFields} } }
  `);
  return data?.myPosts ?? [];
}

export async function fetchMyPlaylists(): Promise<GraphQLPlaylist[]> {
  const data = await graphqlRequest<{ myPlaylists: GraphQLPlaylist[] }>(`
    query MyPlaylists { myPlaylists { ${playlistFields} } }
  `);
  return data?.myPlaylists ?? [];
}

export type PostInput = {
  mediaId: string;
  thumbnailMediaId: string;
  caption?: string;
  collabWith?: string;
  hashtags?: string[];
  audio?: string;
  visibility?: string;
  allowComments?: boolean;
  allowCollabs?: boolean;
  durationSec?: number;
  status?: string;
  scheduledAt?: string;
};

export type PlaylistInput = {
  title: string;
  cover: string;
  itemLabel: string;
};

export type GraphQLLikeResult = {
  liked: boolean;
  likes: number;
};

export async function likePost(id: string): Promise<Result<GraphQLLikeResult>> {
  const result = await graphqlRequestResult<{ likePost: GraphQLLikeResult }>(`
    mutation LikePost($id: UUID!) {
      likePost(id: $id) { liked likes }
    }
  `, { id });
  return result.ok ? { ok: true, value: result.value.likePost } : result;
}

export async function unlikePost(id: string): Promise<Result<GraphQLLikeResult>> {
  const result = await graphqlRequestResult<{ unlikePost: GraphQLLikeResult }>(`
    mutation UnlikePost($id: UUID!) {
      unlikePost(id: $id) { liked likes }
    }
  `, { id });
  return result.ok ? { ok: true, value: result.value.unlikePost } : result;
}

export type GraphQLSaveResult = {
  saved: boolean;
  saves: number;
};

export type GraphQLShareResult = {
  shares: number;
  shared: boolean;
};

export async function sharePost(id: string): Promise<Result<GraphQLShareResult>> {
  const result = await graphqlRequestResult<{ sharePost: GraphQLShareResult }>(`
    mutation SharePost($id: UUID!) {
      sharePost(id: $id) { shares shared }
    }
  `, { id });
  return result.ok ? { ok: true, value: result.value.sharePost } : result;
}

export async function savePost(id: string): Promise<Result<GraphQLSaveResult>> {
  const result = await graphqlRequestResult<{ savePost: GraphQLSaveResult }>(`
    mutation SavePost($id: UUID!) {
      savePost(id: $id) { saved saves }
    }
  `, { id });
  return result.ok ? { ok: true, value: result.value.savePost } : result;
}

export async function unsavePost(id: string): Promise<Result<GraphQLSaveResult>> {
  const result = await graphqlRequestResult<{ unsavePost: GraphQLSaveResult }>(`
    mutation UnsavePost($id: UUID!) {
      unsavePost(id: $id) { saved saves }
    }
  `, { id });
  return result.ok ? { ok: true, value: result.value.unsavePost } : result;
}

export async function createPost(input: PostInput): Promise<GraphQLPost | null> {
  const data = await graphqlRequest<{ createPost: GraphQLPost }>(`
    mutation CreatePost($input: PostInput!) {
      createPost(input: $input) { ${postFields} }
    }
  `, { input });
  return data?.createPost ?? null;
}

export async function updatePost(id: string, input: Partial<PostInput>): Promise<GraphQLPost | null> {
  const data = await graphqlRequest<{ updatePost: GraphQLPost }>(`
    mutation UpdatePost($id: UUID!, $input: UpdatePostInput!) {
      updatePost(id: $id, input: $input) { ${postFields} }
    }
  `, { id, input });
  return data?.updatePost ?? null;
}

export async function deletePost(id: string): Promise<boolean> {
  const data = await graphqlRequest<{ deletePost: boolean }>(`
    mutation DeletePost($id: UUID!) { deletePost(id: $id) }
  `, { id });
  return data?.deletePost ?? false;
}

export async function createPlaylist(input: PlaylistInput): Promise<GraphQLPlaylist | null> {
  const data = await graphqlRequest<{ createPlaylist: GraphQLPlaylist }>(`
    mutation CreatePlaylist($input: PlaylistInput!) {
      createPlaylist(input: $input) { ${playlistFields} }
    }
  `, { input });
  return data?.createPlaylist ?? null;
}

export async function updatePlaylist(id: string, input: Partial<PlaylistInput>): Promise<GraphQLPlaylist | null> {
  const data = await graphqlRequest<{ updatePlaylist: GraphQLPlaylist }>(`
    mutation UpdatePlaylist($id: UUID!, $input: UpdatePlaylistInput!) {
      updatePlaylist(id: $id, input: $input) { ${playlistFields} }
    }
  `, { id, input });
  return data?.updatePlaylist ?? null;
}

export async function deletePlaylist(id: string): Promise<boolean> {
  const data = await graphqlRequest<{ deletePlaylist: boolean }>(`
    mutation DeletePlaylist($id: UUID!) { deletePlaylist(id: $id) }
  `, { id });
  return data?.deletePlaylist ?? false;
}

const profileSummaryFields = `
  id
  username
  displayName
  avatarUrl
  avatarColor
  verified
  collabScore
  collabCount
  followers
  following
  openToCollab
  isFollowing
`;

export async function fetchSuggestedProfiles(limit = 6): Promise<GraphQLProfileSummary[] | null> {
  const data = await graphqlRequest<{ suggestedProfiles: GraphQLProfileSummary[] }>(`
    query SuggestedProfiles($limit: Int!) {
      suggestedProfiles(limit: $limit) { ${profileSummaryFields} }
    }
  `, { limit });
  return data?.suggestedProfiles ?? null;
}

export async function fetchMyFollowing(): Promise<GraphQLProfileSummary[] | null> {
  const data = await graphqlRequest<{ myFollowing: GraphQLProfileSummary[] }>(`
    query MyFollowing { myFollowing { ${profileSummaryFields} } }
  `);
  return data?.myFollowing ?? null;
}

export type GraphQLProfilePage = {
  profiles: GraphQLProfileSummary[];
  nextCursor: string | null;
};

export async function fetchMyFollowingPage(after: string | null, limit = 20): Promise<Result<GraphQLProfilePage>> {
  const result = await graphqlRequestResult<{ myFollowingPage: GraphQLProfilePage }>(`
    query MyFollowingPage($after: String, $limit: Int!) {
      myFollowingPage(after: $after, limit: $limit) {
        nextCursor profiles { ${profileSummaryFields} }
      }
    }
  `, { after, limit });
  return result.ok ? { ok: true, value: result.value.myFollowingPage } : result;
}

export async function fetchMyFollowers(): Promise<GraphQLProfileSummary[] | null> {
  const data = await graphqlRequest<{ myFollowers: GraphQLProfileSummary[] }>(`
    query MyFollowers { myFollowers { ${profileSummaryFields} } }
  `);
  return data?.myFollowers ?? null;
}

export async function fetchMyFollowersPage(after: string | null, limit = 20): Promise<Result<GraphQLProfilePage>> {
  const result = await graphqlRequestResult<{ myFollowersPage: GraphQLProfilePage }>(`
    query MyFollowersPage($after: String, $limit: Int!) {
      myFollowersPage(after: $after, limit: $limit) {
        nextCursor profiles { ${profileSummaryFields} }
      }
    }
  `, { after, limit });
  return result.ok ? { ok: true, value: result.value.myFollowersPage } : result;
}

export async function fetchFollowersPage(username: string, after: string | null, limit = 20): Promise<Result<GraphQLProfilePage>> {
  const result = await graphqlRequestResult<{ followersPage: GraphQLProfilePage }>(`
    query FollowersPage($username: String!, $after: String, $limit: Int!) {
      followersPage(username: $username, after: $after, limit: $limit) {
        nextCursor profiles { ${profileSummaryFields} }
      }
    }
  `, { username, after, limit });
  return result.ok ? { ok: true, value: result.value.followersPage } : result;
}

export async function fetchFollowingPage(username: string, after: string | null, limit = 20): Promise<Result<GraphQLProfilePage>> {
  const result = await graphqlRequestResult<{ followingPage: GraphQLProfilePage }>(`
    query FollowingPage($username: String!, $after: String, $limit: Int!) {
      followingPage(username: $username, after: $after, limit: $limit) {
        nextCursor profiles { ${profileSummaryFields} }
      }
    }
  `, { username, after, limit });
  return result.ok ? { ok: true, value: result.value.followingPage } : result;
}

export type GraphQLFollowResult = {
  following: boolean;
  followers: number;
  followingCount: number;
};

export async function followProfile(identifier: string): Promise<Result<GraphQLFollowResult>> {
  const result = await graphqlRequestResult<{ follow: GraphQLFollowResult }>(`
    mutation Follow($username: String!) {
      follow(username: $username) { following followers followingCount }
    }
  `, { username: identifier });
  return result.ok ? { ok: true, value: result.value.follow } : result;
}

export async function unfollowProfile(identifier: string): Promise<Result<GraphQLFollowResult>> {
  const result = await graphqlRequestResult<{ unfollow: GraphQLFollowResult }>(`
    mutation Unfollow($username: String!) {
      unfollow(username: $username) { following followers followingCount }
    }
  `, { username: identifier });
  return result.ok ? { ok: true, value: result.value.unfollow } : result;
}
