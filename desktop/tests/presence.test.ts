/** The presence state machine: mount/unmount timing for surfaces that animate
 * out. A component that unmounts the moment its `open` flag flips cannot play
 * an exit; this decouples "still mounted" from "is open" so CSS can transition
 * toward a closed target state before the node leaves the tree. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isMounted, nextPhase, presenceState } from '../src/ui/presence.ts'

test('opening mounts on the first commit and settles a phase later', () => {
  assert.equal(nextPhase('closed', true), 'entering')
  assert.equal(nextPhase('entering', true), 'open')
})

test('closing keeps the node mounted through one exit phase', () => {
  assert.equal(nextPhase('open', false), 'exiting')
  assert.equal(presenceState('exiting'), 'closed') // CSS 目标态
  assert.equal(isMounted('exiting'), true) // 但仍在 DOM
})

test('only the timer closes, never a boolean flip', () => {
  // closed 只能靠 hook 的超时到达：任何「已挂载」相位在 wantOpen=false 时
  // 最多走到 exiting，不会直接 closed。
  for (const phase of ['entering', 'open', 'exiting'] as const) {
    assert.equal(nextPhase(phase, false), 'exiting')
  }
  // closed 本身是幂等的，这是唯一一处 nextPhase 返回 closed 的输入
  assert.equal(nextPhase('closed', false), 'closed')
})

test('an exit interrupted by a reopen enters again instead of jumping open', () => {
  assert.equal(nextPhase('exiting', true), 'entering')
  assert.equal(nextPhase('entering', false), 'exiting') // 进入中被打断也能反向
})

test('mounted and the data-state attribute disagree exactly in the exiting phase', () => {
  for (const phase of ['entering', 'open'] as const) {
    assert.equal(isMounted(phase), true)
    assert.equal(presenceState(phase), 'open')
  }
  assert.equal(isMounted('closed'), false)
  assert.equal(presenceState('closed'), 'closed')
})
