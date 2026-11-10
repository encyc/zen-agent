import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  frameBelongsTo,
  frameError,
  frameSessionBelongsTo,
} from '../src/rpc/frames.ts'

test('error frames and rejected results both yield a message', () => {
  assert.equal(frameError({ type: 'error', message: 'boom' }), 'boom')
  assert.equal(frameError({ type: 'result', ok: false, reason: 'nope' }), 'nope')
  assert.equal(frameError({ type: 'result', ok: false }), 'request failed')
  assert.equal(frameError({ type: 'result', ok: true }), null)
})

test('a frame belongs when untagged or tagged with the active project', () => {
  assert.equal(frameBelongsTo({}, '/work/p'), true)
  assert.equal(frameBelongsTo({ project: '' }, '/work/p'), true)
  assert.equal(frameBelongsTo({ project: '/work/p' }, '/work/p'), true)
  assert.equal(frameBelongsTo({ project: '/work/q' }, '/work/p'), false)
})

test('without an active project every frame passes', () => {
  assert.equal(frameBelongsTo({ project: '/work/q' }, null), true)
})

test('a frame belongs to the session on screen, or passes untagged', () => {
  assert.equal(frameSessionBelongsTo({}, '/work/p/s1.jsonl'), true)
  assert.equal(frameSessionBelongsTo({ session: '' }, '/work/p/s1.jsonl'), true)
  assert.equal(
    frameSessionBelongsTo({ session: '/work/p/s1.jsonl' }, '/work/p/s1.jsonl'),
    true,
  )
  // A sibling session of the same project must not paint this pane.
  assert.equal(
    frameSessionBelongsTo({ session: '/work/p/s2.jsonl' }, '/work/p/s1.jsonl'),
    false,
  )
  assert.equal(frameSessionBelongsTo({ session: '/work/p/s2.jsonl' }, null), true)
})
