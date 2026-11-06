/** Mounted-window contract: only the trailing rows of a loaded transcript are
 * rendered, growing upward a step at a time. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  TRANSCRIPT_INITIAL_MOUNT,
  TRANSCRIPT_WINDOW_MIN,
  TRANSCRIPT_WINDOW_STEP,
  growTranscriptWindow,
  reduceTranscriptWindow,
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
