/** Incremental subagent transcript: each poll returns the rows after the
 * cursor (plus the authoritative total), and the panel appends them instead
 * of rebuilding the whole child conversation every second. */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  mergeSubagentPage,
  nextSubagentCursor,
} from '../src/state/subagentPage.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(here, '..', 'src', rel), 'utf8')

test('the first poll adopts the server page', () => {
  assert.deepEqual(
    mergeSubagentPage(null, { transcript: [{ id: 'v1' }, { id: 'v2' }], total: 2 }).views,
    [{ id: 'v1' }, { id: 'v2' }],
  )
})

test('a poll appends only the rows after the cursor', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.deepEqual(mergeSubagentPage(previous, { transcript: [{ id: 'v3' }], total: 3 }), {
    views: [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }],
    resync: false,
  })
})

test('the re-sent streaming tail row replaces its previous copy', () => {
  const previous = [{ id: 'v1', text: 'Hel' }, { id: 'v2', text: 'wor' }]
  // The client keeps the last row in the request so its in-place growth is
  // never frozen; the merge must replace, not duplicate, that row.
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ id: 'v2', text: 'world' }, { id: 'v3', text: '!' }],
      total: 3,
    }).views,
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
    }).views,
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
    }).views,
    [{ id: 'v1' }, { id: 'v2', text: 'patched' }, { id: 'v3' }],
  )
})

test('rows without an id key off their call id', () => {
  const previous = [{ call_id: 'c1', progress: '10%' }]
  assert.deepEqual(
    mergeSubagentPage(previous, {
      transcript: [{ call_id: 'c1', progress: '20%' }],
      total: 1,
    }).views,
    [{ call_id: 'c1', progress: '20%' }],
  )
})

test('an unchanged poll keeps the previous array identity', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  assert.equal(mergeSubagentPage(previous, { transcript: [], total: 2 }).views, previous)
  assert.equal(
    mergeSubagentPage(previous, { transcript: [{ id: 'v2' }], total: 2 }).views,
    previous,
  )
})

test('a short page fills the gap the client is missing', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(
    mergeSubagentPage(previous, { transcript: [{ id: 'v2' }, { id: 'v3' }], total: 3 }).views,
    [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }],
  )
})

test('a shrinking transcript keeps its rows and asks for a resnapshot', () => {
  // An empty assistant row is dropped on the server; the client cursor now
  // points past the shortened transcript, so the page is empty. Substituting
  // it would blank the panel for a poll: keep the rows and resend everything.
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  const merged = mergeSubagentPage(previous, { transcript: [], total: 1 })
  assert.deepEqual(merged.views, previous)
  assert.equal(merged.resync, true)
  // The resync drops the stale cursor: the next poll is a full request.
  assert.equal(nextSubagentCursor(merged.views, merged.resync), undefined)
  // The resnapshot that request brings back is authoritative and lands it.
  const snapshot = mergeSubagentPage(
    merged.views,
    { transcript: [{ id: 'v1' }], total: 1 },
    { fullSnapshot: true },
  )
  assert.deepEqual(snapshot.views, [{ id: 'v1' }])
  assert.equal(snapshot.resync, false)
})

test('a page that cannot describe the rows is kept for a resync', () => {
  const previous = [{ id: 'v1' }, { id: 'v2' }]
  const merged = mergeSubagentPage(previous, {
    transcript: [{ id: 'x' }, { id: 'y' }, { id: 'z' }],
    total: 2,
  })
  assert.deepEqual(merged.views, previous)
  assert.equal(merged.resync, true)
  // A full snapshot is trusted even when its total still disagrees.
  assert.deepEqual(
    mergeSubagentPage(
      previous,
      { transcript: [{ id: 'x' }, { id: 'y' }, { id: 'z' }], total: 2 },
      { fullSnapshot: true },
    ).views,
    [{ id: 'x' }, { id: 'y' }, { id: 'z' }],
  )
})

test('a transcript that emptied keeps the rows until the snapshot lands', () => {
  const previous = [{ id: 'v1' }]
  const merged = mergeSubagentPage(previous, { transcript: [], total: 0 })
  assert.deepEqual(merged.views, previous)
  assert.equal(merged.resync, true)
  assert.deepEqual(
    mergeSubagentPage(previous, { transcript: [], total: 0 }, { fullSnapshot: true }).views,
    [],
  )
})

test('an old server without total is a full snapshot', () => {
  const previous = [{ id: 'v1' }]
  assert.deepEqual(
    mergeSubagentPage(previous, { transcript: [{ id: 'v1' }, { id: 'v2' }] }).views,
    [{ id: 'v1' }, { id: 'v2' }],
  )
  // The same snapshot keeps the array identity (no rebuild every second).
  const snapshot = [{ id: 'v1' }, { id: 'v2' }]
  const held = mergeSubagentPage(null, { transcript: snapshot }).views
  assert.equal(mergeSubagentPage(held, { transcript: [...snapshot] }).views, held)
})

test('the request cursor keeps the mutable rows in range', () => {
  assert.equal(nextSubagentCursor(null), undefined)
  assert.equal(nextSubagentCursor([]), undefined)
  // A pending resync always asks for the whole transcript.
  assert.equal(nextSubagentCursor([{ id: 'v1' }, { id: 'v2' }], true), undefined)
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

test('the panel carries the merge resync into the next request', () => {
  const panel = read('ui/rightbar/SubagentTranscript.tsx')
  assert.match(panel, /nextSubagentCursor\(viewsRef\.current, resync\.current\)/)
  assert.match(panel, /mergeSubagentPage\(viewsRef\.current, result, \{/)
})
