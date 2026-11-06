import type { SessionSummary } from '../rpc/types'

export type SessionScope = 'project' | 'all'

export interface SessionGroup {
  project: string
  sessions: SessionSummary[]
}

export interface SessionSections {
  pinned: SessionSummary[]
  groups: SessionGroup[]
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

export function sessionGroups(
  sessions: SessionSummary[],
  scope: SessionScope,
  query: string,
): SessionSections {
  const needle = query.trim().toLowerCase()
  const visible = sessions.filter((session) => matches(session, needle))
  const pinned = visible.filter((session) => session.pinned).sort(compareSessions)
  const rest = visible.filter((session) => !session.pinned).sort(compareSessions)
  if (scope === 'project') {
    return {
      pinned,
      groups: rest.length === 0 ? [] : [{ project: rest[0].project ?? '', sessions: rest }],
    }
  }
  const byProject = new Map<string, SessionSummary[]>()
  for (const session of rest) {
    const key = session.project ?? ''
    const bucket = byProject.get(key)
    if (bucket === undefined) byProject.set(key, [session])
    else bucket.push(session)
  }
  const groups = [...byProject.entries()].map(([project, groupSessions]) => ({
    project,
    sessions: groupSessions,
  }))
  groups.sort((a, b) => {
    const byUpdated = (b.sessions[0]?.updated_at ?? 0) - (a.sessions[0]?.updated_at ?? 0)
    return byUpdated !== 0 ? byUpdated : a.project.localeCompare(b.project)
  })
  return { pinned, groups }
}
