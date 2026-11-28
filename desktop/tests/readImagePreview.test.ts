/** The fetch decision behind the read_image card's expanded body: a settled
 * call with a path names an image; anything else stays text-only. Run with
 * `node --test tests/readImagePreview.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readImageTarget, type ReadImageTarget } from '../src/ui/chat/readImagePreview.ts'
import type { ToolNode } from '../src/state/chatStore.ts'

function toolNode(overrides: Partial<ToolNode> = {}): ToolNode {
  return {
    kind: 'tool',
    id: 't1',
    callId: 'c1',
    name: 'read_image',
    argsPreview: '{"path": "shot.png"}',
    args: { path: 'shot.png' },
    state: 'ok',
    progress: '',
    resultText: 'shot.png (image/png, 1200 bytes)',
    isError: false,
    durationMs: 12,
    diff: null,
    details: {},
    ...overrides,
  }
}

test('a settled read_image call with a path names one image', () => {
  assert.deepEqual(readImageTarget(toolNode()), { path: 'shot.png' } satisfies ReadImageTarget)
})

test('a relative or tilde path passes through untouched', () => {
  const node = toolNode({ args: { path: '~/shots/shot 2.png' } })
  assert.deepEqual(readImageTarget(node), { path: '~/shots/shot 2.png' })
})

test('another tool never fetches an image', () => {
  assert.equal(readImageTarget(toolNode({ name: 'read_file' })), null)
})

test('a failed call has nothing beyond its IN/OUT card', () => {
  assert.equal(readImageTarget(toolNode({ state: 'error', isError: true })), null)
})

test('a stopped call has nothing to show either', () => {
  assert.equal(readImageTarget(toolNode({ state: 'interrupted' })), null)
  assert.equal(readImageTarget(toolNode({ state: 'timed_out' })), null)
})

test('a running call has no result yet', () => {
  assert.equal(readImageTarget(toolNode({ state: 'running' })), null)
})

test('a missing or empty path argument is not fetchable', () => {
  assert.equal(readImageTarget(toolNode({ args: {} })), null)
  assert.equal(readImageTarget(toolNode({ args: { path: '' } })), null)
  assert.equal(readImageTarget(toolNode({ args: { path: '   ' } })), null)
  assert.equal(readImageTarget(toolNode({ args: { path: 42 } })), null)
})
