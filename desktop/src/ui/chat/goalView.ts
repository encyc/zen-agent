/** Pure goal labels shared by the chip, panel and tests. */

import type { GoalView } from '../../rpc/types'

export function goalStatusIcon(status: string): string {
  switch (status) {
    case 'active':
      return '◎'
    case 'paused':
      return '⏸'
    case 'blocked':
      return '⊘'
    case 'budget_limited':
      return '◼'
    case 'complete':
      return '✓'
    default:
      return '•'
  }
}

export function goalSummary(view: GoalView): string {
  return `${view.objective} · ${view.turns_used}/${view.max_turns}`
}
