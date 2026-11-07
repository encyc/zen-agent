/** Rows for the settings project list: every project the app knows about,
 * whether it has session history, a manual registration, or both. */

import type { SessionSummary } from '../rpc/types'

export interface ProjectStat {
  sessions: number
  last: number
}

export interface RegisteredProject {
  path: string
  name: string
  at: number
}

export interface ProjectRowData {
  /** Displayed and opened path (the registration when there is one). */
  path: string
  /** Canonical project root recorded in the sessions' meta.json. */
  root: string
  name: string
  registered: boolean
  stat: ProjectStat
}

const EMPTY_STAT: ProjectStat = { sessions: 0, last: 0 }

function baseName(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

export function projectRows(
  sessions: Pick<SessionSummary, 'project_path' | 'updated_at'>[],
  entries: RegisteredProject[],
): ProjectRowData[] {
  const stats = new Map<string, ProjectStat>()
  for (const session of sessions) {
    const root = session.project_path ?? ''
    if (root === '') continue
    const previous = stats.get(root) ?? EMPTY_STAT
    stats.set(root, {
      sessions: previous.sessions + 1,
      last: Math.max(previous.last, session.updated_at ?? 0),
    })
  }

  const rows: ProjectRowData[] = []
  for (const [root, stat] of stats) {
    const entry = entries.find(
      (project) => project.path === root || project.path.startsWith(`${root}/`),
    )
    rows.push({
      path: entry?.path ?? root,
      root,
      name: entry?.name ?? baseName(root),
      registered: entry !== undefined,
      stat,
    })
  }

  for (const entry of entries) {
    const known = rows.some(
      (row) => entry.path === row.root || entry.path.startsWith(`${row.root}/`),
    )
    if (known) continue
    rows.push({
      path: entry.path,
      root: entry.path,
      name: entry.name,
      registered: true,
      stat: EMPTY_STAT,
    })
  }

  const lastUsed = (row: ProjectRowData) => {
    if (row.stat.last > 0) return row.stat.last
    const entry = entries.find((project) => project.path === row.path)
    return entry === undefined ? 0 : entry.at / 1000
  }
  rows.sort((a, b) => lastUsed(b) - lastUsed(a) || a.name.localeCompare(b.name))
  return rows
}
