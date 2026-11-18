/** Commit-graph lane layout: greedy first-fit over a newest-first commit list.
 *
 * A `lane` is a column; `slots` maps a column to the hash it is waiting for.
 * A commit takes the lane already expecting it, else the first free column.
 * Its first parent inherits the commit's lane; each extra (merge) parent
 * claims a free column. Columns whose expected hash arrives are reclaimed, so
 * finished branches give their lane back.
 *
 * Rendering needs more than the dot's lane: a row also reports which lanes
 * arrive from above (`incoming`), which leave below (`outgoing`), which pass
 * straight through (`through`) and which converge into the dot because they
 * were also waiting for this commit (`converge`). Drawing only those lanes is
 * what keeps the graph from turning into a solid barcode.
 */

import type { GitCommit } from '../../rpc/types.ts'

export interface LaneEdge {
  hash: string
  lane: number
}

export interface LaneRow {
  hash: string
  lane: number
  parents: LaneEdge[]
  /** Lanes carrying a line into this row from the row above. */
  incoming: number[]
  /** Lanes carrying a line out of this row toward the row below. */
  outgoing: number[]
  /** Lanes that neither start nor end here: a straight vertical segment. */
  through: number[]
  /** Pending lanes that were also waiting for this commit; their lines bend
   * into the dot instead of continuing. */
  converge: number[]
}

export const LANE_COLORS = [
  'var(--dsw-static-blue-500)',
  'var(--dsw-static-green-500)',
  'var(--dsw-static-amber-500)',
  'var(--dsw-static-red-500)',
  'var(--dsw-static-deepseek-500)',
  'var(--dsw-static-neutral-bluish-400)',
] as const

export function assignLanes(commits: GitCommit[]): LaneRow[] {
  const slots: (string | null)[] = []
  const rows: LaneRow[] = []

  const freeLane = (): number => {
    const index = slots.indexOf(null)
    if (index >= 0) return index
    slots.push(null)
    return slots.length - 1
  }
  const occupied = (): number[] =>
    slots.flatMap((hash, lane) => (hash === null ? [] : [lane]))

  for (const commit of commits) {
    const incoming = occupied()
    const waiting = slots.flatMap((hash, lane) => (hash === commit.hash ? [lane] : []))
    // Every lane waiting for this commit converges here. The first one owns
    // the dot; the rest draw a bend into it. Without this, a branch that
    // merges into an ancestor leaves a duplicate lane dangling at the tail.
    const lane = waiting.length > 0 ? waiting[0] : freeLane()
    for (const matched of waiting) slots[matched] = null
    slots[lane] = null

    // Claim this commit's lane for its first parent before any extra parent
    // looks for a free column — otherwise a merge's second parent would take
    // the lane the first parent is about to occupy.
    if (commit.parents.length > 0) slots[lane] = commit.parents[0]

    const parents: LaneEdge[] = []
    commit.parents.forEach((parent, index) => {
      if (index === 0) {
        parents.push({ hash: parent, lane })
        return
      }
      let parentLane = slots.indexOf(parent)
      if (parentLane < 0) parentLane = freeLane()
      slots[parentLane] = parent
      parents.push({ hash: parent, lane: parentLane })
    })

    // Trim trailing empty columns so the row width tracks the live branches.
    while (slots.length > 0 && slots[slots.length - 1] === null) slots.pop()

    const outgoing = occupied()
    const outgoingSet = new Set(outgoing)
    rows.push({
      hash: commit.hash,
      lane,
      parents,
      incoming,
      outgoing,
      through: incoming.filter((value) => value !== lane && outgoingSet.has(value)),
      converge: incoming.filter((value) => value !== lane && !outgoingSet.has(value)),
    })
  }
  return rows
}

/** Refresh merge: new commits (newest first) go in front, already-loaded
 * commits stay in order and keep their place — the reader's scroll position
 * survives an agent-run refresh. */
export function mergeCommits(loaded: GitCommit[], fresh: GitCommit[]): GitCommit[] {
  const seen = new Set(fresh.map((commit) => commit.hash))
  return [...fresh, ...loaded.filter((commit) => !seen.has(commit.hash))]
}
