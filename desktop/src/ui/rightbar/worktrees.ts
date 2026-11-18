/** Branch/worktree ordering for the Git panel's collapsed branch section.
 *
 * A worktree carries no creation time, so the branch tip's commit time is the
 * recency key: a branch cut from today's HEAD sorts first, an idle one sinks.
 * Unknown (unborn / bare) entries sort last, and ties fall back to the branch
 * name so the list is stable between refreshes.
 */

import type { WorktreeEntry } from '../../rpc/types.ts'

export function sortWorktrees(worktrees: WorktreeEntry[]): WorktreeEntry[] {
  return [...worktrees].sort((a, b) => {
    const left = a.timestamp || 0
    const right = b.timestamp || 0
    if (left !== right) return right - left
    const leftName = a.branch || a.head || a.path
    const rightName = b.branch || b.head || b.path
    return leftName.localeCompare(rightName)
  })
}
