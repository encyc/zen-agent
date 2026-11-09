/** Incremental subagent transcript pages.
 *
 * The panel polls one child's conversation while it runs. The server answers
 * with the views after the client's cursor plus the authoritative total, so a
 * poll appends the new rows instead of rebuilding the whole conversation every
 * second. The cursor keeps one row of overlap: the live tail row mutates in
 * place (streamed text, tool progress), and only a re-sent copy shows that.
 */

export interface SubagentPage<T> {
  /** Rows the server returned: everything after the request's `after`. */
  transcript?: T[]
  /** The full row count; a polling client proves it skipped nothing. */
  total?: number
}

/** The `after` cursor for the next poll: one before the end, so the mutable
 * tail row is re-fetched and replaced. Undefined means "send everything". */
export function nextSubagentCursor(views: readonly unknown[] | null): number | undefined {
  if (views === null || views.length === 0) return undefined
  return views.length - 1
}

/** Fold one polled page into the accumulated rows.
 *
 * - first load, or an old server without `total`: adopt the page as-is;
 * - an unchanged poll returns the previous array (same identity, no rebuild);
 * - otherwise the fresh rows replace everything from their start index on,
 *   which covers both appends and the re-sent streaming tail row.
 */
export function mergeSubagentPage<T>(
  previous: readonly T[] | null,
  page: SubagentPage<T>,
): T[] {
  const fresh = page.transcript ?? []
  const total = page.total
  if (previous === null || total === undefined) return [...fresh]
  // The page cannot describe the rows the client holds: trust the server's
  // tail slice rather than duplicating or inventing rows.
  if (total < fresh.length) return [...fresh]
  const start = total - fresh.length
  if (start === 0) return [...fresh]
  if (start > previous.length) return [...fresh]
  if (fresh.length === 0 && total === previous.length) return previous as T[]
  return [...previous.slice(0, start), ...fresh]
}
