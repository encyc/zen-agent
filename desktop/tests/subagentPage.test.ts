/** Incremental subagent transcript: each poll returns the rows after the
 * cursor (plus the authoritative total), and the panel appends them instead
 * of rebuilding the whole child conversation every second. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  mergeSubagentPage,
  nextSubagentCursor,
} from '../src/state/subagentPage.ts'

test('the first poll adopts the server page', () => {
  assert.deepEqual(
    mergeSubagentPage(null, { transcript: [{ id: 'v1' }, { id: 'v2' }], total: 2 }),
    [{ id: 'v1' }, { id: 'v2' }],
  )
})

test('a poll appends only the rows after the cursor', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.deepEqual(mergeSubagentPage(previous, { transcript: [{ id: 'v3' }], total: 3 }), [
    { id: 'v1' },
    { id: 'v2' },
    { id: 'v3' },
  ])
})

test('the re-sent streaming tail row replaces its previous copy', () => {
  const previous = [{ id: 'v1', text: 'Hel' }, { id: 'v2', text: 'wor' }]
  // The client keeps the last row in the request so its in-place growth is
  // never frozen; the merge must replace, not duplicate, that row.
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ id: 'v2', text: 'world' }, { id: 'v3', text: '!' }],
      total: 3,
    }),
    [{ id: 'v1', text: 'Hel' }, { id: 'v2', text: 'world' }, { id: 'v3', text: '!' }],
  )
})

test('an unchanged poll keeps the previous array identity', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.equal(mergeSubagentPage(previous, { transcript: [], total: 2 }), previous)
})

test('a short page fills the gap the client is missing', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(
    mergeSubagentPage(previous, { transcript: [{ id: 'v2' }, { id: 'v3' }], total: 3 }),
    [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }],
  )
})

test('a page starting past the client rows falls back to the server rows', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(mergeSubagentPage(previous, { transcript: [{ id: 'v3' }], total: 3 }), [
    { id: 'v3' },
  ])
})

test('an inconsistent page never duplicates rows', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ id: 'x' }, { id: 'y' }, { id: 'z' }],
      total: 2,
    }),
    [{ id: 'x' }, { id: 'y' }, { id: 'z' }],
  )
})

test('an old server without total is a full snapshot', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(
    mergeSubagentPage(previous, { transcript: [{ id: 'v1' }, { id: 'v2' }] }),
    [{ id: 'v1' }, { id: 'v2' }],
  )
})

test('an empty child clears the accumulated rows', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(mergeSubagentPage(previous, { transcript: [], total: 0 }), [])
})

test('the request cursor keeps one row of overlap for streaming', () => {
  assert.equal(nextSubagentCursor(null), undefined)
  assert.equal(nextSubagentCursor([]), undefined)
  assert.equal(nextSubagentCursor([{ id: 'v1' }]), 0)
  assert.equal(nextSubagentCursor([{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }]), 2)
})
