import assert from 'node:assert/strict'
import { test } from 'node:test'
import { roleMentions, roomSendPreview } from '../src/state/roomPreview.ts'
import type { SwarmAgentState, SwarmRole } from '../src/rpc/types.ts'

const roles = [
  { id: 'research', name: 'Research', supportsVision: true },
  { id: 'writer', name: 'Writer', supportsVision: false },
] as unknown as SwarmRole[]

const agents = [
  { agent: 'research', name: 'Research', state: 'running', task: 't1', pendingApply: false },
] as unknown as SwarmAgentState[]

test('mentions resolve only to known roles', () => {
  assert.deepEqual(roleMentions('@research 和 @ghost 看 @writer', ['research', 'writer']), [
    'research',
    'writer',
  ])
  assert.deepEqual(roleMentions('@research@writer', ['research', 'writer']), ['research'])
})

test('the preview counts tasks and names busy and non-vision targets', () => {
  const preview = roomSendPreview(['research', 'writer'], roles, agents, 1)
  assert.equal(preview.tasks, 2)
  assert.equal(preview.images, 1)
  assert.deepEqual(preview.queued, ['research'])
  assert.deepEqual(preview.noVision, ['writer'])
})

test('no mention means no task and no image send', () => {
  const preview = roomSendPreview([], roles, agents, 2)
  assert.equal(preview.tasks, 0)
  assert.deepEqual(preview.queued, [])
  assert.deepEqual(preview.noVision, [])
})
