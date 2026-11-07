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
  'var(--dsw-static-neutral-bluish-500)',
  'var(--dsw-static-deepseek-500)',
  'var(--dsw-static-blue-450)',
  'var(--dsw-static-green-400)',
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

export interface StatusMeta {
  key: string
  tone: 'run' | 'wait' | 'error' | 'done'
  pulse: boolean
}

const STATUS_TABLE: Record<string, StatusMeta> = {
  running: { key: 'panel.taiji.status.running', tone: 'run', pulse: true },
  ok: { key: 'panel.taiji.status.ok', tone: 'wait', pulse: false },
  error: { key: 'panel.taiji.status.error', tone: 'error', pulse: false },
  merged: { key: 'panel.taiji.status.merged', tone: 'done', pulse: false },
  interrupted: { key: 'panel.taiji.status.interrupted', tone: 'done', pulse: false },
  killed: { key: 'panel.taiji.status.killed', tone: 'done', pulse: false },
}

export function statusMeta(status: string | undefined): StatusMeta | null {
  if (status === undefined) return null
  return STATUS_TABLE[status] ?? null
}

export function relativeTime(ts: number | undefined, now: number = Date.now() / 1000): string {
  if (ts === undefined || ts <= 0) return ''
  const seconds = Math.max(0, Math.floor(now - ts))
  if (seconds < 60) return 'now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
}
