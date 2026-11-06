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
