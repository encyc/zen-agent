/** The presence state machine: mount/unmount timing for surfaces that animate
 * out. A component that unmounts the moment its `open` flag flips cannot play
 * an exit; this decouples "still mounted" from "is open" so CSS can transition
 * toward a closed target state before the node leaves the tree. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { effectiveLeaveMs, inertProps, isMounted, nextPhase, presenceState } from '../src/ui/presence.ts'

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

test('a phase write re-derives from the live intent, never the literal phase', () => {
  // The hook's rAF/timer callbacks both funnel through nextPhase(wantOpen).
  // A close that lands in the same frame as the opener's rAF would otherwise
  // overwrite `exiting` with `open` and strand the overlay visibly open.
  // The pure function is where that invariant is pinned: whatever phase the
  // callback finds, the intent wins.
  assert.equal(nextPhase('entering', false), 'exiting')
  assert.equal(nextPhase('exiting', false), 'exiting')
  assert.equal(nextPhase('exiting', true), 'entering')
})

test('mounted and the data-state attribute disagree exactly in the exiting phase', () => {
  for (const phase of ['entering', 'open'] as const) {
    assert.equal(isMounted(phase), true)
    assert.equal(presenceState(phase), 'open')
  }
  assert.equal(isMounted('closed'), false)
  assert.equal(presenceState('closed'), 'closed')
})

test('a reduced-motion reader unmounts on the next commit instead of waiting', () => {
  // base.css collapses the transition to 0.01ms but cannot shorten the leaving
  // timer: an invisible, inert node would linger for the full window. The window
  // itself has to collapse when the reader asked for no motion.
  assert.equal(effectiveLeaveMs(120, false), 120)
  assert.equal(effectiveLeaveMs(120, true), 0)
})

test('an exiting surface is inert, not merely aria-hidden', () => {
  // aria-hidden on a subtree that still holds focus is an a11y violation and
  // leaves the node focusable by Tab. inert covers focus, AT and pointer at once.
  assert.deepEqual(inertProps(true), { inert: '' })
  assert.deepEqual(inertProps(false), {})
})
