/** Whether to look for a new release, whether the cached answer still counts,
 * and what the banner above the transcript should say. Pure decisions only:
 * the network call and the installer live in `updateStore.ts`. */

export const UPDATE_TTL_MS = 60 * 60 * 1000

export interface UpdateCache {
  /** Epoch ms of the last completed check (successful or not). */
  checkedAt: number
  /** The newest published version the check saw. */
  latest: string
  /** The version the user was already told about, if any. */
  notified: string | null
}

export type UpdatePhase = 'idle' | 'checking' | 'available' | 'installing' | 'failed'

export type UpdateBanner =
  | { kind: 'hidden' }
  | { kind: 'available'; version: string }
  /** The running install cannot replace itself (a .deb): link to the download. */
  | { kind: 'manual'; version: string }
  | { kind: 'blocked'; version: string }
  | { kind: 'installing'; version: string }
  | { kind: 'failed'; message: string }

/** Numeric segment compare: `0.10.0` is newer than `0.9.9`, prefixes ignored. */
export function compareVersions(a: string, b: string): -1 | 0 | 1 {
  const segments = (value: string) =>
    value
      .trim()
      .replace(/^v/i, '')
      .split(/[-+]/)[0]
      .split('.')
      .map((part) => {
        const parsed = Number.parseInt(part, 10)
        return Number.isFinite(parsed) ? parsed : 0
      })
  const left = segments(a)
  const right = segments(b)
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const l = left[index] ?? 0
    const r = right[index] ?? 0
    if (l !== r) return l < r ? -1 : 1
  }
  return 0
}

/** A cached version is worth showing only when it is newer and not yet shown. */
export function shouldOffer(cache: UpdateCache, current: string): boolean {
  if (cache.latest === '') return false
  if (compareVersions(cache.latest, current) <= 0) return false
  return cache.notified !== cache.latest
}

export function cacheIsFresh(cache: UpdateCache, now: number): boolean {
  return now - cache.checkedAt < UPDATE_TTL_MS
}

/** A failed lookup still counts as a look: the TTL keeps an offline machine
 * from paying the same timeout on every launch. */
export function cacheAfterFailure(
  cache: UpdateCache | null,
  now: number,
  current: string,
): UpdateCache {
  return {
    checkedAt: now,
    latest: cache?.latest ?? current,
    notified: cache?.notified ?? null,
  }
}

/** Installing restarts the app, so never offer it mid-turn or over an approval. */
export function bannerFor(input: {
  cache: UpdateCache | null
  current: string
  enabled: boolean
  busy: boolean
  pendingApprovals: number
  phase: UpdatePhase
  /** False when this install cannot be replaced in place (e.g. a .deb). */
  selfUpdatable?: boolean
}): UpdateBanner {
  const offer =
    input.cache !== null && shouldOffer(input.cache, input.current) ? input.cache.latest : null
  if (input.phase === 'installing') {
    return offer === null ? { kind: 'hidden' } : { kind: 'installing', version: offer }
  }
  // The switch wins over a failed check: turning updates off must silence the
  // error too, or a user who opted out keeps seeing it.
  if (!input.enabled) return { kind: 'hidden' }
  if (input.phase === 'failed') return { kind: 'failed', message: 'update.failed' }
  if (offer === null) return { kind: 'hidden' }
  if (input.busy || input.pendingApprovals > 0) return { kind: 'blocked', version: offer }
  if (input.phase === 'available') {
    return input.selfUpdatable === false
      ? { kind: 'manual', version: offer }
      : { kind: 'available', version: offer }
  }
  return { kind: 'hidden' }
}
