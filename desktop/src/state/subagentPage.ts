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
 * everything".
 */
export function nextSubagentCursor(
  views: readonly unknown[] | null,
): number | undefined {
  if (views === null || views.length === 0) return undefined
  for (let index = 0; index < views.length; index += 1) {
    const row = views[index]
    if (typeof row === 'object' && row !== null && (row as RowShape).status === 'running') {
      return index
    }
  }
  return views.length - 1
}

/** Fold one polled page into the accumulated rows by row identity.
 *
 * - first load, or an old server without `total`: adopt the page as-is;
 * - a rewound/inconsistent page (fewer authoritative rows than the client
 *   holds) replaces the accumulation instead of duplicating it;
 * - otherwise every row whose identity is already accumulated is replaced in
 *   place and the rest are appended — a page may carry an earlier card that
 *   settled, so slicing by `total - fresh.length` would misfile it;
 * - when nothing changed the previous array is returned (same identity, no
 *   rebuild).
 */
export function mergeSubagentPage<T>(
  previous: readonly T[] | null,
  page: SubagentPage<T>,
): T[] {
  const fresh = page.transcript ?? []
  const total = page.total
  if (previous === null) return [...fresh]
  // An old server sends the whole snapshot: keep the identity when it is the
  // same conversation, so the poll still skips the rebuild.
  if (total === undefined) {
    return sameRows(previous, fresh) ? (previous as T[]) : [...fresh]
  }
  if (total === 0) return previous.length === 0 ? (previous as T[]) : []
  // The page cannot describe the rows the client holds: trust the server's
  // slice rather than duplicating or inventing rows.
  if (total < fresh.length || total < previous.length) return [...fresh]

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
  return changed ? merged : (previous as T[])
}
