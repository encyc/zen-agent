/** Commit-graph lane layout: greedy first-fit over a newest-first commit list.
 *
 * A `lane` is a column; `slots` maps a column to the hash it is waiting for.
 * A commit takes the lane already expecting it, else the first free column.
 * Its first parent inherits the commit's lane; each extra (merge) parent
 * claims a free column. Columns whose expected hash arrives are reclaimed, so
 * finished branches give their lane back.
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
}

export const LANE_COLORS = [
  'var(--dsw-alias-state-success-primary)',
  'var(--dsw-alias-state-info-primary)',
  'var(--dsw-alias-state-warning-primary)',
  'var(--dsw-alias-state-error-primary)',
  'var(--dsw-alias-label-secondary)',
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

  for (const commit of commits) {
    let lane = slots.indexOf(commit.hash)
    if (lane >= 0) {
      // Arriving at a lane we were waiting for: clear it, the commit owns it.
      slots[lane] = null
    } else {
      lane = freeLane()
    }

    // Claim this commit's lane for its first parent before any extra parent
    // looks for a free column — otherwise a merge's second parent would take
    // the lane the first parent is about to occupy.
    if (commit.parents.length > 0) slots[lane] = commit.parents[0]
    else slots[lane] = null

    const parents: LaneEdge[] = []
    commit.parents.forEach((parent, index) => {
      if (index === 0) {
        parents.push({ hash: parent, lane })
        return
      }
      let parentLane = slots.indexOf(parent)
      if (parentLane < 0) parentLane = freeLane()
      parents.push({ hash: parent, lane: parentLane })
    })

    // Trim trailing empty columns so the row width tracks the live branches.
    while (slots.length > 0 && slots[slots.length - 1] === null) slots.pop()
    rows.push({ hash: commit.hash, lane, parents })
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
