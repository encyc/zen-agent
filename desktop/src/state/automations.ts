/**
 * Pure view-model helpers for the automations panel.
 *
 * Kept out of the component so `node --test` can exercise them without a DOM:
 * the panel itself is a thin renderer over these two functions.
 */

export interface AutomationRow {
  project: string
  name: string
  prompt: string
  schedule: string
  enabled: boolean
  timeout_s: number
  session: string
  next_due: number
  last_status: string
  last_skip: string
  last_error: string
  last_run_at: number
  missed: number
  run_count: number
  cost: number
}

const HOUR = 3600
const MINUTE = 60

/**
 * Group rows by project, projects ordered by their most urgent job.
 *
 * Insertion order is the display order, so the project with the soonest due
 * job comes first — the same "most overdue first" rule the daemon schedules by.
 */
export function groupByProject(rows: AutomationRow[]): Map<string, AutomationRow[]> {
  const groups = new Map<string, AutomationRow[]>()
  for (const row of rows) {
    const bucket = groups.get(row.project)
    if (bucket === undefined) groups.set(row.project, [row])
    else bucket.push(row)
  }
  for (const bucket of groups.values()) {
    bucket.sort((a, b) => urgency(a) - urgency(b))
  }
  return new Map([...groups.entries()].sort((a, b) => urgency(a[1][0]) - urgency(b[1][0])))
}

function urgency(row: AutomationRow): number {
  if (row.enabled !== true) return Number.POSITIVE_INFINITY
  return row.next_due > 0 ? row.next_due : Number.POSITIVE_INFINITY
}

/**
 * A short "when", for a table cell.
 *
 * `0` means the schedule could not be resolved (a disabled job), and `now`
 * covers everything already due — a disabled job never reads as urgent.
 */
export function formatNextDue(next_due: number, now: number): string {
  if (!Number.isFinite(next_due) || next_due <= 0) return '—'
  const delta = next_due - now
  if (delta <= 0) return 'now'
  if (delta < HOUR) return `${Math.max(1, Math.round(delta / MINUTE))}m`
  return `${Math.round(delta / HOUR)}h`
}
