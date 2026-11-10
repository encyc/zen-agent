import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pendingForProject } from '../src/state/pendingProject.ts'

test('only the active project and untagged entries render', () => {
  const queue = [
    { requestId: 'legacy' },
    { requestId: 'p', project: '/work/p' },
    { requestId: 'q', project: '/work/q' },
  ]

  assert.deepEqual(
    pendingForProject(queue, '/work/p').map((entry) => entry.requestId),
    ['legacy', 'p'],
  )
  assert.deepEqual(
    pendingForProject(queue, null).map((entry) => entry.requestId),
    ['legacy', 'p', 'q'],
  )
})

test('a sibling session of the same project stays queued until it is on screen', () => {
  const queue = [
    { requestId: 'legacy' },
    { requestId: 's1', project: '/work/p', session: '/work/p/s1.jsonl' },
  ]

  assert.deepEqual(
    pendingForProject(queue, '/work/p', '/work/p/s1.jsonl').map(
      (entry) => entry.requestId,
    ),
    ['legacy', 's1'],
  )
  assert.deepEqual(
    pendingForProject(queue, '/work/p', '/work/p/s2.jsonl').map(
      (entry) => entry.requestId,
    ),
    ['legacy'],
  )
  // Without a known active session every session of the project renders.
  assert.deepEqual(
    pendingForProject(queue, '/work/p', null).map((entry) => entry.requestId),
    ['legacy', 's1'],
  )
})
