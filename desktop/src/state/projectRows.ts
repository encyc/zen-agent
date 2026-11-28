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
  /** Sessions put away in the archive sidecar; never in `stat.sessions`. */
  archived: number
}

const EMPTY_STAT: ProjectStat = { sessions: 0, last: 0 }

function baseName(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

export function projectRows(
  sessions: Pick<SessionSummary, 'project_path' | 'updated_at'>[],
  entries: RegisteredProject[],
  archivedSessions: Pick<SessionSummary, 'project_path'>[] = [],
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

  const archivedCounts = new Map<string, number>()
  for (const session of archivedSessions) {
    const root = session.project_path ?? ''
    if (root === '') continue
    archivedCounts.set(root, (archivedCounts.get(root) ?? 0) + 1)
  }

  const rows: ProjectRowData[] = []
  const makeRow = (
    root: string,
    stat: ProjectStat,
    entry: RegisteredProject | undefined,
  ): ProjectRowData => ({
    path: entry?.path ?? root,
    root,
    name: entry?.name ?? baseName(root),
    registered: entry !== undefined,
    stat,
    archived: archivedCounts.get(root) ?? 0,
  })
  for (const [root, stat] of stats) {
    rows.push(
      makeRow(
        root,
        stat,
        entries.find(
          (project) => project.path === root || project.path.startsWith(`${root}/`),
        ),
      ),
    )
  }
  // A project whose every session is archived still deserves its row: the
  // settings page is where its history comes back from.
  for (const [root] of archivedCounts) {
    if (stats.has(root)) continue
    rows.push(makeRow(root, EMPTY_STAT, undefined))
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
      archived: 0,
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
