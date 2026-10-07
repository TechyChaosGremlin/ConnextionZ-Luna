import { Loader2, Megaphone, RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";
import { Avatar } from "./profile-ui";
import { useTheme } from "./ThemeContext";
import { ACCENT, EmptyState, SubPage, useTokens, type Tokens } from "./settings-ui";
import { usePaidFeed, type PaidFeedState } from "./paid-feed-store.ts";
import type { PaidFeedItem } from "./paid-feed-graphql.ts";

export function PaidDiscoveryLink({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} aria-label="Open Paid Discovery"
      className="px-3 py-1.5 rounded-full text-[11px] font-bold"
      style={{ background: "rgba(0,174,239,0.15)", color: ACCENT, border: `1px solid ${ACCENT}` }}>
      Paid Discovery
    </button>
  );
}

function PaidPostCard({ item, t }: { item: PaidFeedItem; t: Tokens }) {
  const label = item.isSponsored && item.sponsoredLabel?.trim()
    ? item.sponsoredLabel
    : "Paid";
  return (
    <article aria-label={`${label} post by @${item.creator.username}`}
      className="overflow-hidden rounded-2xl" style={{ background: t.groupBg, border: t.groupBorder }}>
      <div className="flex items-center gap-3 p-4">
        <Avatar src={item.creator.avatarUrl ?? undefined} name={item.creator.displayName}
          color={item.creator.avatarColor ?? undefined} size={40} />
        <div className="min-w-0 flex-1">
          <h2 className="font-bold text-[14px] truncate" style={{ color: t.heading }}>
            {item.creator.displayName}
          </h2>
          <p className="text-[12px] truncate" style={{ color: t.sub }}>@{item.creator.username}</p>
        </div>
        <span aria-label={`Paid content: ${label}`}
          className="px-3 py-1 rounded-full text-[12px] font-bold"
          style={{ color: t.heading, background: t.chipBg, border: t.chipBorder }}>
          {label}
        </span>
      </div>
      {item.mediaUrl ? (
        <video src={item.mediaUrl} poster={item.thumbnail || undefined} controls playsInline
          preload="none" aria-label={`Paid video by @${item.creator.username}`}
          className="w-full max-h-[440px] object-contain bg-black" />
      ) : item.thumbnail ? (
        <img src={item.thumbnail} alt={`Post by @${item.creator.username}`}
          loading="lazy" className="w-full max-h-[440px] object-contain" />
      ) : null}
      {item.caption && (
        <p className="p-4 text-[14px] leading-relaxed whitespace-pre-wrap break-words"
          style={{ color: t.body }}>{item.caption}</p>
      )}
    </article>
  );
}

export function PaidDiscoveryContent({ feed, t }: { feed: PaidFeedState; t: Tokens }) {
  const unavailable = feed.error === "Paid feed delivery is disabled";
  if (feed.status === "loading") {
    return (
      <div role="status" className="flex items-center justify-center gap-2 py-16" style={{ color: t.body }}>
        <Loader2 aria-hidden="true" className="w-5 h-5 animate-spin motion-reduce:animate-none" />
        Loading Paid discovery...
      </div>
    );
  }
  if (feed.status === "error" || unavailable) {
    return (
      <div role="alert" className="text-center">
        <EmptyState t={t} icon={<Megaphone aria-hidden="true" className="w-7 h-7" />}
          title={unavailable ? "Paid discovery is unavailable" : "Paid discovery couldn't load"}
          body={unavailable
            ? "Paid feed delivery is disabled. Sponsored discovery is not available yet."
            : feed.error} />
        <button type="button" onClick={() => void feed.reload()}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-[13px] font-bold"
          style={{ background: t.chipBg, border: t.chipBorder, color: t.heading }}>
          <RefreshCw aria-hidden="true" className="w-4 h-4" /> Try again
        </button>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {feed.items.length === 0 ? (
        <div role="status">
          <EmptyState t={t} icon={<Megaphone aria-hidden="true" className="w-7 h-7" />}
            title="No Paid posts available"
            body="There are no sponsored posts to discover right now." />
        </div>
      ) : (
        <div aria-label="Paid discovery posts" className="space-y-4">
          {feed.items.map((item) => <PaidPostCard key={item.id} item={item} t={t} />)}
        </div>
      )}
      {feed.error && <p role="alert" className="text-center text-[13px]" style={{ color: t.body }}>{feed.error}</p>}
      {!feed.reachedEnd && (
        <div className="flex flex-col items-center gap-2">
          {feed.loadingMore && <p role="status" style={{ color: t.body }}>Loading more Paid posts...</p>}
          <button type="button" disabled={feed.loadingMore} onClick={() => void feed.loadMore()}
            className="px-5 py-2.5 rounded-full text-[13px] font-bold disabled:opacity-50"
            style={{ background: t.chipBg, border: t.chipBorder, color: t.heading }}>
            {feed.loadingMore ? "Loading more..." : feed.error ? "Retry loading more" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}

export function PaidDiscoveryView({ feed, onBack }: { feed: PaidFeedState; onBack: () => void }) {
  const t = useTokens(useTheme());
  const region = useRef<HTMLElement>(null);
  useEffect(() => { region.current?.focus(); }, []);
  return (
    <section ref={region} tabIndex={-1} aria-label="Paid Discovery" className="absolute inset-0 z-30"
      onWheel={(event) => event.stopPropagation()}
      onTouchStart={(event) => event.stopPropagation()}
      onTouchEnd={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}>
      <SubPage title="Paid Discovery" subtitle="A separate space for paid and sponsored posts."
        t={t} onBack={onBack}>
        <PaidDiscoveryContent feed={feed} t={t} />
      </SubPage>
    </section>
  );
}

export function PaidDiscoveryScreen({ viewerKey, onBack }: { viewerKey: string; onBack: () => void }) {
  const feed = usePaidFeed(viewerKey);
  return <PaidDiscoveryView feed={feed} onBack={onBack} />;
}
