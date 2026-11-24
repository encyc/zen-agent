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
  return `${view.objective} · ${view.turns_used}`
}

/** The objective inside a controller continuation prompt, for compact rows. */
export function goalObjective(text: string): string {
  const start = text.indexOf('<objective>')
  if (start < 0) return text.split('\n')[0] ?? ''
  const from = start + '<objective>'.length
  const end = text.indexOf('</objective>', from)
  return (end >= 0 ? text.slice(from, end) : text.slice(from)).trim()
}
