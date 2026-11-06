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
