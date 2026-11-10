/** Zen roster presentation helpers: how the sidebar splits and paints the
 * fleet. Pure functions so node --test can pin them without a DOM. */

import type { RosterItem } from '../rpc/types.ts'

export type ZenStatusTone = 'active' | 'blocked' | 'settled'

const STATUS_TONES: Record<string, ZenStatusTone> = {
  working: 'active',
  queued: 'active',
  blocked: 'blocked',
  failed: 'blocked',
  waiting: 'settled',
  idle: 'settled',
}

const KNOWN_STATUSES = Object.keys(STATUS_TONES)

export function splitRoster(roster: RosterItem[]): {
  router: RosterItem | null
  workers: RosterItem[]
} {
  const router = roster.find((row) => row.role === 'router') ?? null
  const workers = roster.filter((row) => row !== router)
  return { router, workers }
}

export function zenStatusKey(status: string): string {
  return KNOWN_STATUSES.includes(status) ? `zen.status.${status}` : 'zen.status.idle'
}

export function zenStatusTone(status: string): ZenStatusTone {
  return STATUS_TONES[status] ?? 'settled'
}
