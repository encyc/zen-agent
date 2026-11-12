/** Pure projections for the work item panel.
 *
 * The panel renders from the room store's `works` plus the rounds already in
 * `tasks` (the room list carries both), so these functions stay testable
 * without a DOM: ordering, round slicing and the badge key of a state. */

import type { SwarmTask, SwarmWork, SwarmWorkState } from '../rpc/types.ts'

/** Panel order: active jobs first, archive last, newest first within a state. */
export const WORK_STATE_ORDER: readonly SwarmWorkState[] = ['open', 'review', 'closed']

export function sortWorks(works: SwarmWork[]): SwarmWork[] {
  return [...works].sort((a, b) => {
    const rank =
      WORK_STATE_ORDER.indexOf(a.state) - WORK_STATE_ORDER.indexOf(b.state)
    if (rank !== 0) return rank
    return b.openedAt - a.openedAt
  })
}

export function roundsOf(tasks: SwarmTask[], workItemId: string): SwarmTask[] {
  return tasks
    .filter((task) => task.workItemId === workItemId)
    .sort((a, b) => a.createdAt - b.createdAt)
}

export function workBadgeKey(state: SwarmWorkState): string {
  return `swarm.workState.${state}`
}

/** A merged item is kept for bookkeeping; its rounds belong to the target. */
export function isScorable(work: SwarmWork): boolean {
  return work.mergedInto === null
}

/** The score cell: a number, or the "unrated" key for a null score. */
export function scoreText(work: SwarmWork): string | null {
  return work.score === null ? null : String(work.score)
}
