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
