/** How much of a loaded transcript stays mounted (pi-desktop D261 parity).
 *
 * The host sends the whole transcript, but mounting every historical row keeps
 * a React tree, rendered markdown and sanitized HTML alive per row — and every
 * streamed delta re-renders all of them. A long session therefore drags the
 * paint rate down. The mounted history is a trailing window: the tail (where
 * the live turn is) always mounts, and reaching the top grows the window one
 * step at a time.
 */

/** Rows mounted in the first commit after a session switch: fast first paint. */
export const TRANSCRIPT_INITIAL_MOUNT = 15

/** Steady-state cap on mounted history rows. */
export const TRANSCRIPT_WINDOW_MIN = 60

/** Rows added each time the user reaches the top of the mounted window. */
export const TRANSCRIPT_WINDOW_STEP = 40

/** Rows requested for one wire page: the first load and every older page.
 * A long session fetches its tail first and pages backwards from there. */
export const TRANSCRIPT_PAGE_SIZE = 60

export interface TranscriptWindowInput {
  /** Loaded transcript rows (all nodes the snapshot produced). */
  historyLength: number
  /** Current steady-state budget, grown by `growTranscriptWindow`. */
  windowSize: number
  /** Whether this is the bounded first commit after a session switch. */
  initialCommit: boolean
}

export interface TranscriptWindow {
  /** Trailing rows to mount. */
  mounted: number
  /** Loaded rows above the window, deliberately not mounted. */
  hiddenAbove: number
  /** Whether any loaded row is being withheld. */
  bounded: boolean
}

/** Resolve the mounted slice for one render. */
export function reduceTranscriptWindow({
  historyLength,
  windowSize,
  initialCommit,
}: TranscriptWindowInput): TranscriptWindow {
  const length = Math.max(0, historyLength)
  const budget = initialCommit
    ? TRANSCRIPT_INITIAL_MOUNT
    : Math.max(TRANSCRIPT_WINDOW_MIN, windowSize)
  const mounted = Math.min(length, budget)
  const hiddenAbove = length - mounted
  return { mounted, hiddenAbove, bounded: hiddenAbove > 0 }
}

export interface EarlierRow {
  /** Whether the row renders above the first mounted turn. */
  visible: boolean
  /** Rows named in the button: withheld rows, else one wire page. */
  count: number
  /** Whether a click fetches an older page instead of growing the mount. */
  loadsPage: boolean
}

/** The top-of-transcript affordance. Loaded history withheld by the mounted
 * window grows on click; once every loaded row is mounted but the server still
 * holds older pages, the same row must fetch one — otherwise that cursor is
 * reachable only by a scroll-to-top gesture with no visible target. */
export function earlierRow(
  bounded: boolean,
  hiddenAbove: number,
  hasMoreBefore: boolean,
): EarlierRow {
  if (bounded) {
    const count = Math.max(0, hiddenAbove)
    return { visible: true, count, loadsPage: false }
  }
  if (hasMoreBefore) {
    return { visible: true, count: TRANSCRIPT_PAGE_SIZE, loadsPage: true }
  }
  return { visible: false, count: 0, loadsPage: false }
}

/** Next window budget when the user reaches the top of the mounted rows. */
export function growTranscriptWindow(
  windowSize: number,
  historyLength: number,
): number {
  const current = Math.max(TRANSCRIPT_WINDOW_MIN, windowSize)
  const length = Math.max(0, historyLength)
  if (current >= length) return current
  return Math.min(length, current + TRANSCRIPT_WINDOW_STEP)
}

/** The mounted rows: the trailing `mounted` entries, live tail included. */
export function tailWindow<T>(items: T[], mounted: number): T[] {
  if (mounted <= 0) return []
  return mounted >= items.length ? items : items.slice(-mounted)
}

/** The absolute index of the first loaded view, counted from the session
 * start. `before` on the wire is that index, so a tail page of a long session
 * starts at `total - page length` — not at zero and not at its own length. A
 * server without `total` sends the whole snapshot, which starts at zero. */
export function firstLoadedIndex(total: number | undefined, loaded: number): number {
  const count = Math.max(0, Math.trunc(total ?? loaded))
  const length = Math.max(0, loaded)
  return Math.max(0, count - length)
}

/** The `before` cursor for the next older wire page, or null when the loaded
 * snapshot already reaches the start of the session. The cursor counts views
 * loaded from the start, so live appends at the tail never shift it. */
export function olderPageCursor(
  hasMoreBefore: boolean,
  firstLoaded: number,
): number | null {
  if (!hasMoreBefore || firstLoaded <= 0) return null
  return firstLoaded
}

/** The first loaded index after prepending a page of `pageLength` rows. Each
 * older page walks the absolute range down by exactly the rows it delivered. */
export function prependFirstIndex(firstLoaded: number, pageLength: number): number {
  return Math.max(0, firstLoaded - Math.max(0, pageLength))
}

/** Budget after the reader returns to the tail: release the rows mounted
 * while browsing upward. A budget already below the floor (the first commit)
 * is left alone. */
export function shrinkTranscriptWindow(windowSize: number): number {
  const current = Math.max(0, windowSize)
  return current <= TRANSCRIPT_WINDOW_MIN ? current : TRANSCRIPT_WINDOW_MIN
}

