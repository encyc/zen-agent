/** Update policy: pure decisions about whether to look for a release, whether
 * the cached answer still counts, and what the sidebar notification says. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  UPDATE_TTL_MS,
  cacheAfterFailure,
  cacheIsFresh,
  compareVersions,
  shouldOffer,
  updateNotice,
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

test('the notice waits for an idle app before offering to restart', () => {
  const input = {
    cache: cache('0.9.0'),
    current: '0.8.1',
    enabled: true,
    busy: true,
    pendingApprovals: 0,
    phase: 'available' as const,
  }
  assert.deepEqual(updateNotice(input), { kind: 'blocked', version: '0.9.0' })
})

test('a disabled switch hides everything except an explicit install in flight', () => {
  const base = { cache: cache('0.9.0'), current: '0.8.1', busy: false, pendingApprovals: 0 }
  assert.deepEqual(updateNotice({ ...base, enabled: false, phase: 'available' }), { kind: 'hidden' })
  assert.deepEqual(updateNotice({ ...base, enabled: true, phase: 'available' }), {
    kind: 'available',
    version: '0.9.0',
  })
  assert.deepEqual(updateNotice({ ...base, enabled: true, phase: 'failed' }), {
    kind: 'failed',
    message: 'update.failed',
  })
})

test('a stale cache does not resurrect a version the user already installed', () => {
  assert.deepEqual(
    updateNotice({
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

test('a failed check is silent when the switch is off', () => {
  const base = { cache: null, current: '0.8.1', busy: false, pendingApprovals: 0, phase: 'failed' as const }
  assert.deepEqual(updateNotice({ ...base, enabled: false }), { kind: 'hidden' })
  assert.deepEqual(updateNotice({ ...base, enabled: true }), { kind: 'failed', message: 'update.failed' })
})

test('a failed check still stamps the cache so the next launch waits the TTL', () => {
  const stamped = cacheAfterFailure(cache('0.9.0', '0.9.0', 10), 5_000, '0.8.1')
  assert.deepEqual(stamped, { checkedAt: 5_000, latest: '0.9.0', notified: '0.9.0' })
  assert.equal(cacheIsFresh(stamped, 5_000), true)
})

test('a failure with no cache records the running version, not a phantom release', () => {
  assert.deepEqual(cacheAfterFailure(null, 5_000, '0.8.1'), {
    checkedAt: 5_000,
    latest: '0.8.1',
    notified: null,
  })
})

test('a pending approval blocks the restart as firmly as a running turn', () => {
  assert.deepEqual(
    updateNotice({
      cache: cache('0.9.0'),
      current: '0.8.1',
      enabled: true,
      busy: false,
      pendingApprovals: 1,
      phase: 'available',
    }),
    { kind: 'blocked', version: '0.9.0' },
  )
})

test('an install the updater cannot replace offers the download page instead', () => {
  const base = {
    cache: cache('0.9.0'),
    current: '0.8.1',
    enabled: true,
    busy: false,
    pendingApprovals: 0,
    phase: 'available' as const,
    selfUpdatable: false,
  }
  assert.deepEqual(updateNotice(base), { kind: 'manual', version: '0.9.0' })
  assert.deepEqual(updateNotice({ ...base, busy: true }), { kind: 'blocked', version: '0.9.0' })
})
