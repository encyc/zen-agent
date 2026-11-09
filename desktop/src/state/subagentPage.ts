/** Incremental subagent transcript pages.
 *
 * The panel polls one child's conversation while it runs. The server answers
 * with the views after the client's cursor (plus any earlier card that is
 * still mutable) and the authoritative total, so a poll patches the rows it
 * already shows instead of rebuilding the whole conversation every second.
 */

export interface SubagentPage<T> {
  /** Rows the server returned: everything after the request's `after`, plus
   * any earlier row that is still mine to patch (an unfinished card). */
  transcript?: T[]
  /** The full row count; a polling client proves it skipped nothing. */
  total?: number
}

interface RowShape {
  id?: unknown
  call_id?: unknown
  status?: unknown
}

/** Row identity: the view id (`v{n}`), or the call id for a row that predates
 * stable ids. Null means the row cannot be matched to an accumulated one. */
function rowKey(row: unknown): string | null {
  if (typeof row !== 'object' || row === null) return null
  const record = row as RowShape
  if (typeof record.id === 'string' && record.id !== '') return `id:${record.id}`
  if (typeof record.call_id === 'string' && record.call_id !== '') {
    return `call:${record.call_id}`
  }
  return null
}

function sameRow(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function sameRows(left: readonly unknown[], right: readonly unknown[]): boolean {
  if (left.length !== right.length) return false
  for (let index = 0; index < left.length; index += 1) {
    if (!sameRow(left[index], right[index])) return false
  }
  return true
}

/** The `after` cursor for the next poll.
 *
 * It stays on the earliest card that is still `running`, so that card keeps
 * being re-read until its settlement has landed; older cards that already
 * settled sit before the cursor and need no traffic. With no running card,
 * the cursor keeps one row of overlap: the live tail row mutates in place
 * (streamed text), and only a re-sent copy shows that. Undefined means "send
 * everything" — first load, an empty panel, or a pending resync (the held
 * cursor points past a server transcript that shrank).
 */
export function nextSubagentCursor(
  views: readonly unknown[] | null,
  resync = false,
): number | undefined {
  if (resync) return undefined
  if (views === null || views.length === 0) return undefined
  for (let index = 0; index < views.length; index += 1) {
    const row = views[index]
    if (typeof row === 'object' && row !== null && (row as RowShape).status === 'running') {
      return index
    }
  }
  return views.length - 1
}

/** What one polled page means for the panel: the rows to render now, and
 * whether the next poll has to resend the whole transcript. */
export interface SubagentMerge<T> {
  /** The rows to render; never blanked by a page that shrank (stale by one
   * poll beats an empty panel). */
  views: T[]
  /** The page cannot describe the rows the panel holds (the server transcript
   * shrank under the cursor): keep `views` and ask for a full snapshot next. */
  resync: boolean
}

export interface SubagentMergeOptions {
  /** The request carried no `after` cursor, so the page is the whole
   * transcript and is authoritative even when it is shorter than what the
   * panel holds (that is how a resync converges). */
  fullSnapshot?: boolean
}

/** Fold one polled page into the accumulated rows by row identity.
 *
 * - first load, or an old server without `total`: adopt the page as-is;
 * - a full snapshot is authoritative, even when it is shorter (the resnapshot
 *   a resync asked for);
 * - a page that cannot describe the rows the panel holds (the transcript
 *   shrank, e.g. an empty assistant row was dropped) keeps the accumulated
 *   rows and requests a resync instead of substituting the partial page —
 *   otherwise an empty tail page would blank the panel for a poll;
 * - otherwise every row whose identity is already accumulated is replaced in
 *   place and the rest are appended — a page may carry an earlier card that
 *   settled, so slicing by `total - fresh.length` would misfile it;
 * - when nothing changed the previous array is returned (same identity, no
 *   rebuild).
 */
export function mergeSubagentPage<T>(
  previous: readonly T[] | null,
  page: SubagentPage<T>,
  options: SubagentMergeOptions = {},
): SubagentMerge<T> {
  const fresh = page.transcript ?? []
  const total = page.total
  if (previous === null) return { views: [...fresh], resync: false }
  // An old server sends the whole snapshot: keep the identity when it is the
  // same conversation, so the poll still skips the rebuild.
  if (total === undefined) {
    return {
      views: sameRows(previous, fresh) ? (previous as T[]) : [...fresh],
      resync: false,
    }
  }
  // The whole transcript came back: its rows are the truth, even when fewer
  // than the panel holds. This is what makes a resync converge.
  if (options.fullSnapshot === true) {
    return {
      views: sameRows(previous, fresh) ? (previous as T[]) : [...fresh],
      resync: false,
    }
  }
  if (total < fresh.length || total < previous.length) {
    return { views: previous as T[], resync: true }
  }

  const merged = [...previous]
  const indexOf = new Map<string, number>()
  merged.forEach((row, index) => {
    const key = rowKey(row)
    if (key !== null) indexOf.set(key, index)
  })
  let changed = false
  for (const row of fresh) {
    const key = rowKey(row)
    if (key === null) {
      merged.push(row)
      changed = true
      continue
    }
    const at = indexOf.get(key)
    if (at === undefined) {
      indexOf.set(key, merged.length)
      merged.push(row)
      changed = true
      continue
    }
    if (!sameRow(merged[at], row)) {
      merged[at] = row
      changed = true
    }
  }
  return { views: changed ? merged : (previous as T[]), resync: false }
}

