/** Incremental reveal for the files tree and memory list: first batch on open,
 * one more batch per "load more" click. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  FILES_REVEAL_BATCH,
  MEMORY_REVEAL_BATCH,
  revealedCount,
} from '../src/state/reveal.ts'

test('the first batch sizes are two hundred files and one hundred notes', () => {
  assert.equal(FILES_REVEAL_BATCH, 200)
  assert.equal(MEMORY_REVEAL_BATCH, 100)
})

test('the first page never reveals more than the loaded rows', () => {
  assert.equal(revealedCount(12, 1, FILES_REVEAL_BATCH), 12)
  assert.equal(revealedCount(0, 1, MEMORY_REVEAL_BATCH), 0)
  assert.equal(revealedCount(500, 1, FILES_REVEAL_BATCH), FILES_REVEAL_BATCH)
})

test('each load more adds one batch and stops at the loaded rows', () => {
  assert.equal(revealedCount(450, 2, FILES_REVEAL_BATCH), 400)
  assert.equal(revealedCount(450, 3, FILES_REVEAL_BATCH), 450)
  assert.equal(revealedCount(250, 2, MEMORY_REVEAL_BATCH), 200)
  assert.equal(revealedCount(250, 99, MEMORY_REVEAL_BATCH), 250)
})

test('stale page state falls back to the first batch', () => {
  assert.equal(revealedCount(500, 0, FILES_REVEAL_BATCH), FILES_REVEAL_BATCH)
  assert.equal(revealedCount(500, -2, FILES_REVEAL_BATCH), FILES_REVEAL_BATCH)
})
