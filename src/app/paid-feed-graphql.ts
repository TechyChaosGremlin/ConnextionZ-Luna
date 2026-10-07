import type { Result } from "./auth-store.ts";
import { graphqlRequestResult, type GraphQLFeedItem } from "./profile-graphql.ts";

export type PaidFeedItem = GraphQLFeedItem & {
  isSponsored: boolean;
  sponsoredLabel: string | null;
  paidCampaignId: string | null;
  paidDeliveryId: string | null;
  paidInteractionContext: string | null;
};

export interface PaidFeedPage {
  items: PaidFeedItem[];
  /** Server-generated and opaque; null means the page sequence is exhausted. */
  nextCursor: string | null;
}

export const PAID_FEED_QUERY = `
  query PaidFeed($cursor: String, $limit: Int!) {
    feed(cursor: $cursor, limit: $limit, filter: { algorithm: PAID }) {
      nextCursor
      items {
        id thumbnail mediaUrl caption views likes isLiked hashtags audio visibility
        allowComments allowCollabs durationSec comments shares saves isSaved isShared collabWith
        isSponsored sponsoredLabel paidCampaignId paidDeliveryId paidInteractionContext
        creator {
          id username displayName avatarUrl avatarColor verified collabScore collabCount
          followers following openToCollab
        }
      }
    }
  }
`;

/** Viewer identity/auth refresh comes from the shared client, never query variables. */
export async function fetchPaidFeedPage(
  cursor: string | null = null,
  limit = 10,
): Promise<Result<PaidFeedPage>> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    return { ok: false, error: "Paid feed page size must be between 1 and 100." };
  }
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return { ok: false, error: "You're offline. Reconnect to load the Paid feed." };
  }
  const result = await graphqlRequestResult<{ feed: PaidFeedPage | null }>(
    PAID_FEED_QUERY,
    { cursor, limit },
  );
  if (!result.ok) return result;
  const page = result.value?.feed;
  if (
    !page ||
    !Array.isArray(page.items) ||
    !(page.nextCursor === null || typeof page.nextCursor === "string")
  ) {
    return { ok: false, error: "The server sent back an unexpected Paid feed response." };
  }
  return { ok: true, value: page };
}
