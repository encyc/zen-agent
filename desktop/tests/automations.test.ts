// desktop/tests/automations.test.ts
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { formatNextDue, groupByProject } from '../src/state/automations.ts'
import { PROJECT_OVERRIDE_EXEMPT, SESSION_TAG_EXEMPT } from '../src/rpc/client.ts'

test('groupByProject keeps projects together, most urgent first', () => {
  const rows = [
    { project: '/a', name: 'r', next_due: 200, enabled: true },
    { project: '/b', name: 's', next_due: 100, enabled: true },
    { project: '/a', name: 't', next_due: 300, enabled: true },
  ] as never
  const groups = groupByProject(rows)
  assert.deepEqual([...groups.keys()], ['/b', '/a'])
  assert.deepEqual(
    groups.get('/a')!.map((r) => r.name),
    ['r', 't'],
  )
})

test('a disabled job sorts last and never reads as urgent', () => {
  const rows = [
    { project: '/a', name: 'off', next_due: 50, enabled: false },
    { project: '/a', name: 'on', next_due: 900, enabled: true },
  ] as never
  const [bucket] = [...groupByProject(rows).values()]
  assert.deepEqual(
    bucket.map((r) => r.name),
    ['on', 'off'],
  )
})

test('formatNextDue says "now" for due-or-past and a short delta otherwise', () => {
  assert.equal(formatNextDue(0, 1000), '—')
  assert.equal(formatNextDue(900, 1000), 'now')
  assert.equal(formatNextDue(1000 + 90, 1000), '2m')
  assert.equal(formatNextDue(1000 + 7200, 1000), '2h')
})

test('automations requests are session-free and keep their own project', () => {
  // The client tags every non-exempt request with the on-screen session and
  // overwrites `project` with the active slot. Both would silently retarget a
  // panel request: the job would be pinned to the transcript the user happens
  // to have open, and a row from another project would be edited in the current
  // one. This test pins the two exemptions that prevent it.
  assert.equal(SESSION_TAG_EXEMPT.has('automations'), true)
  assert.equal(PROJECT_OVERRIDE_EXEMPT.has('automations'), true)
})
