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

/** What a polled view needs to prove it is the same row as before: message
 * rows carry `id`, tool rows fall back to their call id, and the live tail
 * row's mutable fields name the content that streams in place. */
export interface TranscriptViewIdentity {
  id?: string
  call_id?: string
  text?: string
  thinking?: string | null
  thinking_seconds?: number | null
  progress?: string
  result_text?: string
  status?: string
  duration_ms?: number | null
}

function viewIdentity(view: TranscriptViewIdentity): string {
  const identity = view.id ?? view.call_id ?? ''
  if (identity === '') return ''
  // The builder appends the live assistant row once and then mutates its text
  // (and tool rows their progress) under the same id; the tail signature must
  // include that content or the poll would freeze a streaming row.
  return [
    identity,
    view.text ?? '',
    view.thinking ?? '',
    view.thinking_seconds ?? '',
    view.progress ?? '',
    view.result_text ?? '',
    view.status ?? '',
    view.duration_ms ?? '',
  ].join('\u0000')
}

/** Whether a polled transcript can keep the rows already on screen. The wire
 * sends the whole child transcript every second; the same row count with the
 * same trailing row — identity and content — means there is nothing to
 * rebuild. */
export function sameTranscriptTail(
  previous: readonly TranscriptViewIdentity[] | null,
  next: readonly TranscriptViewIdentity[],
): boolean {
  if (previous === null || previous.length !== next.length) return false
  if (next.length === 0) return true
  const before = viewIdentity(previous[previous.length - 1])
  const after = viewIdentity(next[next.length - 1])
  // A row with no identity at all cannot prove it is unchanged.
  return before !== '' && before === after
}
