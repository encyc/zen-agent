/** Pure helpers behind the Taiji panel: lane palette, rail edges and the
 * session↔branch overlay. No React and no RPC here, so the binding rules are
 * unit-testable on their own. */

import type {
  TaijiBranch,
  TaijiCommit,
  TaijiGraph,
  TaijiProjectSession,
  TaijiSessionMap,
} from '../../rpc/types'

export const LANE_PALETTE = [
  'var(--dsw-alias-state-success-primary)',
  'var(--dsw-alias-button-info-fill)',
  'var(--dsw-alias-state-warn-primary)',
  'var(--dsw-alias-state-error-primary)',
] as const

export function laneColor(lane: number): string {
  return LANE_PALETTE[((lane % LANE_PALETTE.length) + LANE_PALETTE.length) % LANE_PALETTE.length]
}

export interface GraphEdge {
  fromLane: number
  toLane: number
  merge: boolean
}

export function graphEdges(commits: TaijiCommit[]): GraphEdge[] {
  const laneOf = new Map(commits.map((commit) => [commit.sha, commit.lane]))
  const edges: GraphEdge[] = []
  for (const commit of commits) {
    for (const parent of commit.parents) {
      const fromLane = laneOf.get(parent)
      if (fromLane === undefined) continue
      edges.push({ fromLane, toLane: commit.lane, merge: commit.parents.length > 1 })
    }
  }
  return edges
}

export interface TaijiRow {
  commit: TaijiCommit
  branch: TaijiBranch | undefined
  sessions: TaijiProjectSession[]
  isHere: boolean
}

export function attachSessions(
  graph: TaijiGraph,
  sessions: TaijiSessionMap,
  currentSessionPath: string | undefined,
): TaijiRow[] {
  const rows: TaijiRow[] = graph.commits.map((commit) => ({
    commit,
    branch: graph.branches.find((entry) => entry.sha === commit.sha),
    sessions: [],
    isHere: false,
  }))
  const rowByBranch = new Map(
    rows.filter((row) => row.branch !== undefined).map((row) => [row.branch!.name, row]),
  )
  for (const project of sessions.projects) {
    if (project.branch === null || project.sessions.length === 0) continue
    const row = rowByBranch.get(project.branch)
    if (row === undefined) continue
    row.sessions.push(...project.sessions)
  }
  for (const row of rows) {
    row.sessions.sort((one, two) => two.updated_at - one.updated_at)
    row.isHere =
      currentSessionPath !== undefined &&
      row.sessions.some((session) => currentSessionPath.endsWith(`/${session.name}.jsonl`))
  }
  return rows
}
