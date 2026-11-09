/** Mounted-window contract: only the trailing rows of a loaded transcript are
 * rendered, growing upward a step at a time. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  TRANSCRIPT_INITIAL_MOUNT,
  TRANSCRIPT_PAGE_SIZE,
  TRANSCRIPT_WINDOW_MIN,
  TRANSCRIPT_WINDOW_STEP,
  growTranscriptWindow,
  olderPageCursor,
  reduceTranscriptWindow,
  sameTranscriptTail,
  shrinkTranscriptWindow,
  tailWindow,
} from '../src/state/transcriptWindow.ts'

test('a short transcript mounts in full', () => {
  const window = reduceTranscriptWindow({
    historyLength: 40,
    windowSize: TRANSCRIPT_WINDOW_MIN,
    initialCommit: false,
  })
  assert.deepEqual(window, { mounted: 40, hiddenAbove: 0, bounded: false })
})

test('an empty transcript mounts nothing', () => {
  const window = reduceTranscriptWindow({
    historyLength: 0,
    windowSize: TRANSCRIPT_WINDOW_MIN,
    initialCommit: true,
  })
  assert.deepEqual(window, { mounted: 0, hiddenAbove: 0, bounded: false })
})

test('a long transcript mounts the steady-state cap', () => {
  const window = reduceTranscriptWindow({
    historyLength: 100,
    windowSize: TRANSCRIPT_WINDOW_MIN,
    initialCommit: false,
  })
  assert.equal(window.mounted, TRANSCRIPT_WINDOW_MIN)
  assert.equal(window.hiddenAbove, 100 - TRANSCRIPT_WINDOW_MIN)
  assert.equal(window.bounded, true)
})

test('the first commit after a switch mounts the initial slice', () => {
  const window = reduceTranscriptWindow({
    historyLength: 100,
    windowSize: TRANSCRIPT_WINDOW_MIN,
    initialCommit: true,
  })
  assert.equal(window.mounted, TRANSCRIPT_INITIAL_MOUNT)
  assert.equal(window.hiddenAbove, 100 - TRANSCRIPT_INITIAL_MOUNT)
  assert.equal(window.bounded, true)
})

test('a budget below the floor is raised to the floor', () => {
  const window = reduceTranscriptWindow({
    historyLength: 100,
    windowSize: 0,
    initialCommit: false,
  })
  assert.equal(window.mounted, TRANSCRIPT_WINDOW_MIN)
})

test('growing adds one step, never past the loaded history', () => {
  assert.equal(growTranscriptWindow(TRANSCRIPT_WINDOW_MIN, 500), TRANSCRIPT_WINDOW_MIN + TRANSCRIPT_WINDOW_STEP)
  assert.equal(growTranscriptWindow(TRANSCRIPT_WINDOW_MIN + TRANSCRIPT_WINDOW_STEP, 100), 100)
  assert.equal(growTranscriptWindow(TRANSCRIPT_WINDOW_MIN, 20), TRANSCRIPT_WINDOW_MIN)
  assert.equal(growTranscriptWindow(0, 0), TRANSCRIPT_WINDOW_MIN)
})

test('the trailing slice always keeps the live tail', () => {
  const rows = ['a', 'b', 'c', 'd', 'e']
  assert.deepEqual(tailWindow(rows, 2), ['d', 'e'])
  assert.deepEqual(tailWindow(rows, 10), rows)
  assert.deepEqual(tailWindow(rows, 0), [])
})

test('a poll with the same row count and tail identity skips the rebuild', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.equal(sameTranscriptTail(previous, [{ id: 'v1' }, { id: 'v2' }]), true)
  assert.equal(sameTranscriptTail(previous, [{ id: 'v1' }, { id: 'v3' }]), false)
  assert.equal(sameTranscriptTail(previous, [{ id: 'v1' }]), false)
  assert.equal(sameTranscriptTail(null, previous), false)
  assert.equal(sameTranscriptTail([], []), true)
})

test('a tool row without a stable id keys off its call id', () => {
  const previous = [{ id: 'v1' }, { call_id: 'c1' }]
  assert.equal(sameTranscriptTail(previous, [{ id: 'v1' }, { call_id: 'c1' }]), true)
  assert.equal(sameTranscriptTail(previous, [{ id: 'v1' }, { call_id: 'c2' }]), false)
  // A row with neither identity cannot prove it is unchanged.
  assert.equal(sameTranscriptTail([{}], [{}]), false)
})

test('a streaming tail row with a stable id still forces a rebuild', () => {
  const previous = [{ id: 'v1' }, { id: 'v2', text: 'Hel', status: 'running' }]
  assert.equal(
    sameTranscriptTail(previous, [{ id: 'v1' }, { id: 'v2', text: 'Hello', status: 'running' }]),
    false,
  )
  assert.equal(
    sameTranscriptTail([{ call_id: 'c1', progress: '10%' }], [{ call_id: 'c1', progress: '20%' }]),
    false,
  )
  assert.equal(
    sameTranscriptTail(previous, [{ id: 'v1' }, { id: 'v2', text: 'Hel', status: 'running' }]),
    true,
  )
})

test('the first wire page is bounded so a long session paints the tail fast', () => {
  assert.equal(TRANSCRIPT_PAGE_SIZE, 60)
})

test('a snapshot with more history before it yields the older-page cursor', () => {
  assert.equal(olderPageCursor(true, 60), 60)
  assert.equal(olderPageCursor(true, 120), 120)
})

test('no cursor when the loaded snapshot already reaches the session start', () => {
  assert.equal(olderPageCursor(false, 60), null)
  assert.equal(olderPageCursor(true, 0), null)
  assert.equal(olderPageCursor(false, 0), null)
})

test('returning to the tail shrinks the window back to the steady-state cap', () => {
  assert.equal(shrinkTranscriptWindow(140), TRANSCRIPT_WINDOW_MIN)
  assert.equal(shrinkTranscriptWindow(TRANSCRIPT_WINDOW_MIN), TRANSCRIPT_WINDOW_MIN)
  // A pre-first-commit budget is below the floor and stays untouched.
  assert.equal(shrinkTranscriptWindow(TRANSCRIPT_INITIAL_MOUNT), TRANSCRIPT_INITIAL_MOUNT)
})
