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

test('an earlier card that settled is replaced in place', () => {
  // A page can carry rows from before the tail (the server re-sends cards
  // that were still running); the merge must patch that row where it sits,
  // not append a duplicate at the end.
  const previous = [
    { id: 'v1', status: 'running', result_text: '' },
    { id: 'v2', status: 'ok' },
  ]
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ id: 'v1', status: 'ok', result_text: 'done' }],
      total: 2,
    }),
    [
      { id: 'v1', status: 'ok', result_text: 'done' },
      { id: 'v2', status: 'ok' },
    ],
  )
})

test('a row the client does not hold is appended by identity', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ id: 'v2', text: 'patched' }, { id: 'v3' }],
      total: 3,
    }),
    [{ id: 'v1' }, { id: 'v2', text: 'patched' }, { id: 'v3' }],
  )
})

test('rows without an id key off their call id', () => {
  const previous = [{ call_id: 'c1', progress: '10%' }]
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ call_id: 'c1', progress: '20%' }],
      total: 1,
    }),
    [{ call_id: 'c1', progress: '20%' }],
  )
})

test('an unchanged poll keeps the previous array identity', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.equal(mergeSubagentPage(previous, { transcript: [], total: 2 }), previous)
  assert.equal(
    mergeSubagentPage(previous, { transcript: [{ id: 'v2' }], total: 2 }),
    previous,
  )
})

test('a short page fills the gap the client is missing', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(
    mergeSubagentPage(previous, { transcript: [{ id: 'v2' }, { id: 'v3' }], total: 3 }),
    [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }],
  )
})

test('a rewound transcript replaces the accumulated rows', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }]
  assert.deepEqual(mergeSubagentPage(previous, { transcript: [{ id: 'n1' }], total: 1 }), [
    { id: 'n1' },
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
  // The same snapshot keeps the array identity (no rebuild every second).
  const snapshot = [{ id: 'v1' }, { id: 'v2' }]
  const held = mergeSubagentPage(null, { transcript: snapshot })
  assert.equal(mergeSubagentPage(held, { transcript: [...snapshot] }), held)
})

test('an empty child clears the accumulated rows', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(mergeSubagentPage(previous, { transcript: [], total: 0 }), [])
})

test('the request cursor keeps the mutable rows in range', () => {
  assert.equal(nextSubagentCursor(null), undefined)
  assert.equal(nextSubagentCursor([]), undefined)
  assert.equal(nextSubagentCursor([{ id: 'v1' }]), 0)
  assert.equal(nextSubagentCursor([{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }]), 2)
  // An earlier card still running must not fall behind the cursor: the next
  // poll has to re-read it until its settlement has landed.
  assert.equal(
    nextSubagentCursor([
      { id: 'v1', status: 'running' },
      { id: 'v2', status: 'ok' },
      { id: 'v3', status: 'running' },
    ]),
    0,
  )
  assert.equal(
    nextSubagentCursor([
      { id: 'v1', status: 'ok' },
      { id: 'v2', status: 'running' },
      { id: 'v3' },
    ]),
    1,
  )
  assert.equal(
    nextSubagentCursor([
      { id: 'v1', status: 'ok' },
      { id: 'v2', status: 'error' },
    ]),
    1,
  )
})
