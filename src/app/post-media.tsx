import { useEffect, useRef, useState } from "react";
import { isProtectedMediaUrl, resolvePostMediaUrl } from "./profile-graphql";

export function useMediaSource(src: string) {
  const [attempt, setAttempt] = useState(0);
  const renewed = useRef(false);
  const [state, setState] = useState({
    source: src, url: isProtectedMediaUrl(src) ? "" : src,
    error: src ? "" : "This post has no media reference.",
  });
  useEffect(() => { renewed.current = false; }, [src]);
  useEffect(() => {
    let active = true;
    setState({ source: src, url: isProtectedMediaUrl(src) ? "" : src, error: "" });
    resolvePostMediaUrl(src).then((result) => {
      if (active) setState({
        source: src, url: result.ok ? result.value : "", error: result.ok ? "" : result.error,
      });
    });
    return () => { active = false; };
  }, [src, attempt]);
  return {
    url: state.source === src ? state.url : "",
    error: state.source === src ? state.error : "",
    onError: () => {
      if (!renewed.current && isProtectedMediaUrl(src)) {
        renewed.current = true;
        setAttempt((value) => value + 1);
      }
      else setState({ source: src, url: "", error: "This post's media could not be loaded." });
    },
    retry: () => {
      renewed.current = false;
      setAttempt((value) => value + 1);
    },
  };
}

export function isVideoMedia(mediaType?: string | null, mediaUrl?: string | null): boolean {
  return mediaType ? mediaType === "video" || mediaType.startsWith("video/") : !!mediaUrl;
}

export function PostMedia({
  src, mediaType, alt, className, controls = false, isActive = false, paused = false,
}: {
  src: string;
  mediaType?: string | null;
  alt: string;
  className?: string;
  controls?: boolean;
  isActive?: boolean;
  paused?: boolean;
}) {
  const media = useMediaSource(src);
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (!video.current || controls) return;
    if (isActive && !paused) void video.current.play().catch(() => {});
    else video.current.pause();
  }, [isActive, paused, controls, media.url]);

  if (media.error) return (
    <div role="alert" className={`${className ?? ""} flex flex-col items-center justify-center gap-2 bg-black/80 text-white text-sm p-4`}>
      <span>{media.error}</span>
      <button type="button" onClick={(event) => { event.stopPropagation(); media.retry(); }}>Retry media</button>
    </div>
  );
  if (!media.url) return <div role="status" className={className}>Loading media...</div>;
  return isVideoMedia(mediaType, src)
    ? <video ref={video} src={media.url} controls={controls} loop={!controls} muted={!controls}
        playsInline preload={controls ? "none" : "metadata"} aria-label={alt} onError={media.onError} className={className} />
    : <img src={media.url} alt={alt} onError={media.onError} className={className} />;
}
