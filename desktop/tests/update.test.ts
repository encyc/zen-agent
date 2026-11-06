/** Update policy: pure decisions about whether to look for a release, whether
 * the cached answer still counts, and what the banner should say. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  UPDATE_TTL_MS,
  bannerFor,
  cacheIsFresh,
  compareVersions,
  shouldOffer,
  type UpdateCache,
} from '../src/state/update.ts'

const cache = (latest: string, notified: string | null = null, checkedAt = 0): UpdateCache => ({
  checkedAt,
  latest,
  notified,
})

test('versions compare numerically, not as strings', () => {
  assert.equal(compareVersions('0.10.0', '0.9.9'), 1)
  assert.equal(compareVersions('v0.8.1', '0.8.1'), 0)
  assert.equal(compareVersions('0.8', '0.8.0'), 0)
  assert.equal(compareVersions('0.7.9', '0.8.0'), -1)
})

test('a cache entry equal to the running version is never offered', () => {
  assert.equal(shouldOffer(cache('0.8.1'), '0.8.1'), false)
  assert.equal(shouldOffer(cache('0.9.0'), '0.8.1'), true)
  assert.equal(shouldOffer(cache('0.9.0', '0.9.0'), '0.8.1'), false)
})

test('the cache goes stale after an hour', () => {
  assert.equal(cacheIsFresh(cache('0.9.0', null, 1_000), 1_000 + UPDATE_TTL_MS - 1), true)
  assert.equal(cacheIsFresh(cache('0.9.0', null, 1_000), 1_000 + UPDATE_TTL_MS), false)
})

test('the banner waits for an idle app before offering to restart', () => {
  const input = {
    cache: cache('0.9.0'),
    current: '0.8.1',
    enabled: true,
    busy: true,
    pendingApprovals: 0,
    phase: 'available' as const,
  }
  assert.deepEqual(bannerFor(input), { kind: 'blocked', version: '0.9.0' })
})

test('a disabled switch hides everything except an explicit install in flight', () => {
  const base = { cache: cache('0.9.0'), current: '0.8.1', busy: false, pendingApprovals: 0 }
  assert.deepEqual(bannerFor({ ...base, enabled: false, phase: 'available' }), { kind: 'hidden' })
  assert.deepEqual(bannerFor({ ...base, enabled: true, phase: 'available' }), {
    kind: 'available',
    version: '0.9.0',
  })
  assert.deepEqual(bannerFor({ ...base, enabled: true, phase: 'failed' }), {
    kind: 'failed',
    message: 'update.failed',
  })
})

test('a stale cache does not resurrect a version the user already installed', () => {
  assert.deepEqual(
    bannerFor({
      cache: cache('0.8.1'),
      current: '0.8.1',
      enabled: true,
      busy: false,
      pendingApprovals: 0,
      phase: 'available',
    }),
    { kind: 'hidden' },
  )
})
