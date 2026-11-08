/** Pure helpers behind the dispatch board: grouping workers by how much a
 * person needs to act, status tones, ages and diff evidence. No React and no
 * RPC here, so the board's ordering rules are unit-testable on their own. */

import type { TaijiDiffFile, TaijiWorker } from '../../rpc/types'

export interface BoardGroups {
  /** Workers asking a question — the only true blockers. */
  attention: TaijiWorker[]
  /** Finished, unmerged, unattended: waiting for an accept/reject call. */
  acceptance: TaijiWorker[]
  /** Mid-turn right now. */
  running: TaijiWorker[]
  /** Spawned but holding for a slot. */
  queued: TaijiWorker[]
  /** Settled: merged/killed/failed — context, not action. */
  history: TaijiWorker[]
}

const HISTORY_STATUSES = new Set(['merged', 'killed', 'error', 'interrupted'])

const recentFirst = (one: TaijiWorker, two: TaijiWorker) => two.updated_at - one.updated_at

/** The board's one organizing principle: sort by how much the person has to
 * do. Attention outranks acceptance; history trails for context. */
export function groupWorkers(workers: TaijiWorker[]): BoardGroups {
  const groups: BoardGroups = {
    attention: [],
    acceptance: [],
    running: [],
    queued: [],
    history: [],
  }
  for (const worker of workers) {
    if (worker.attention !== null) groups.attention.push(worker)
    else if (worker.status === 'ok') groups.acceptance.push(worker)
    else if (worker.status === 'running') groups.running.push(worker)
    else if (worker.status === 'queued') groups.queued.push(worker)
    else if (HISTORY_STATUSES.has(worker.status)) groups.history.push(worker)
    else groups.history.push(worker) // unknown statuses never vanish
  }
  for (const list of [groups.attention, groups.acceptance, groups.running, groups.queued, groups.history]) {
    list.sort(recentFirst)
  }
  return groups
}

export type StatusTone = 'run' | 'wait' | 'done' | 'fail' | 'plain'

const TONES: Record<string, StatusTone> = {
  running: 'run',
  queued: 'wait',
  ok: 'done',
  error: 'fail',
  interrupted: 'fail',
  merged: 'plain',
  killed: 'plain',
}

export function statusTone(status: string): StatusTone {
  return TONES[status] ?? 'plain'
}

/** A task's wall-clock age, structured so the panel renders it in the
 * interface language: a ticking mm:ss under an hour, then coarse spans. */
export type TaskAge =
  | { kind: 'tick'; text: string }
  | { kind: 'hours'; hours: number; minutes: number }
  | { kind: 'days'; days: number }

export function taskAge(createdAt: number, now: number): TaskAge {
  const seconds = Math.max(0, Math.floor(now - createdAt))
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) {
    return { kind: 'tick', text: `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` }
  }
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return { kind: 'hours', hours, minutes: minutes % 60 }
  return { kind: 'days', days: Math.floor(hours / 24) }
}

/** The acceptance card's diff evidence, tallied for i18n rendering. */
export function diffSummary(
  diffstat: TaijiDiffFile[] | null,
): { adds: number; dels: number; count: number } | null {
  if (diffstat === null || diffstat.length === 0) return null
  let adds = 0
  let dels = 0
  for (const file of diffstat) {
    adds += file.adds
    dels += file.dels
  }
  return { adds, dels, count: diffstat.length }
}

/** Compact, locale-neutral relative time for history rows. */
export function relativeTime(ts: number, now: number = Date.now() / 1000): string {
  const seconds = Math.max(0, Math.floor(now - ts))
  if (seconds < 60) return 'now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}
