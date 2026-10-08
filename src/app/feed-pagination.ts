export function shouldLoadNextFeedPage(
  index: number,
  itemCount: number,
  cursor: string | null,
  reachedEnd: boolean,
): boolean {
  return (
    !reachedEnd
    && index >= itemCount - 2
    && (itemCount > 0 || cursor !== null)
  );
}
