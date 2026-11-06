import type { SessionSummary } from '../rpc/types'

/** pi shows this many rows per project before the "load more" row. */
export const PROJECT_PAGE_SIZE = 10

export interface SessionGroup {
  project: string
  sessions: SessionSummary[]
}

export interface SidebarSections {
  pinned: SessionSummary[]
  /** Sessions outside any project; `project` is `''`. */
  standalone: SessionSummary[]
  projects: SessionGroup[]
}

export interface ProjectEntry {
  path: string
  name: string
  at: number
}

export interface SidebarProjectGroup extends SessionGroup {
  /** Registered project folder, or null for a session-only group. */
  path: string | null
}

export function projectName(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

/** Attaches registered folders to their session groups and keeps empty
 * registered projects in the rail so a folder can be opened before it has any
 * sessions. */
export function mergeProjects(
  groups: SessionGroup[],
  entries: ProjectEntry[],
  query: string,
): SidebarProjectGroup[] {
  const needle = query.trim().toLowerCase()
  const merged: SidebarProjectGroup[] = groups
    .filter(
      (group) =>
        needle === '' ||
        group.project.toLowerCase().includes(needle) ||
        group.sessions.some((session) => matches(session, needle)),
    )
    .map((group) => ({
      ...group,
      path:
        group.sessions.find((session) => (session.project_path ?? '') !== '')?.project_path ??
        null,
    }))
  const byName = new Map(merged.map((group) => [group.project, group]))
  for (const entry of entries) {
    const existing = byName.get(entry.name)
    if (existing !== undefined) {
      existing.path = entry.path
      continue
    }
    if (needle !== '' && !entry.name.toLowerCase().includes(needle)) continue
    const group: SidebarProjectGroup = {
      project: entry.name,
      path: entry.path,
      sessions: [],
    }
    byName.set(entry.name, group)
    merged.push(group)
  }
  return merged
}

export function compareSessions(a: SessionSummary, b: SessionSummary): number {
  const byUpdated = (b.updated_at ?? 0) - (a.updated_at ?? 0)
  return byUpdated !== 0 ? byUpdated : a.path.localeCompare(b.path)
}

function matches(session: SessionSummary, query: string): boolean {
  return [session.title, session.name, session.project].some((value) =>
    (value ?? '').toLowerCase().includes(query),
  )
}

/** Splits the flat list into pi's three sidebar sections. Pinned rows are
 * global and never repeat inside a project group. */
export function sessionSections(
  sessions: SessionSummary[],
  query: string,
): SidebarSections {
  const needle = query.trim().toLowerCase()
  const visible = sessions.filter((session) => matches(session, needle))
  const pinned = visible.filter((session) => session.pinned).sort(compareSessions)
  const rest = visible.filter((session) => !session.pinned).sort(compareSessions)

  const standalone = rest.filter((session) => (session.project ?? '') === '')
  const byProject = new Map<string, SessionSummary[]>()
  for (const session of rest) {
    const key = session.project ?? ''
    if (key === '') continue
    const bucket = byProject.get(key)
    if (bucket === undefined) byProject.set(key, [session])
    else bucket.push(session)
  }
  const projects = [...byProject.entries()]
    .map(([project, groupSessions]) => ({ project, sessions: groupSessions }))
    .sort((a, b) => {
      const byUpdated = (b.sessions[0]?.updated_at ?? 0) - (a.sessions[0]?.updated_at ?? 0)
      return byUpdated !== 0 ? byUpdated : a.project.localeCompare(b.project)
    })

  return { pinned, standalone, projects }
}
