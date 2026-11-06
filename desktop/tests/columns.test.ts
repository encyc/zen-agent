import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  computeColumns,
  sidebarMax,
} from '../src/ui/columns.ts'

test('sidebar parity defaults, clamps and viewport cap', () => {
  assert.equal(SIDEBAR_DEFAULT, 275)
  assert.equal(SIDEBAR_MIN, 240)
  assert.equal(SIDEBAR_MAX, 520)
  assert.equal(sidebarMax(1280), 520)
  assert.equal(sidebarMax(700), 380)
  assert.equal(sidebarMax(500), 240)
  assert.equal(computeColumns(1280, 275, 0).sidebar, 275)
  assert.equal(computeColumns(1280, 100, 0).sidebar, 240)
  assert.equal(computeColumns(1280, 900, 0).sidebar, 520)
  assert.equal(computeColumns(700, 900, 0).sidebar, 380)
  assert.equal(computeColumns(500, 900, 0).sidebar, 240)
})

test('collapsed width and the rightbar solve are unchanged', () => {
  assert.equal(computeColumns(1280, 0, 0).sidebar, 56)
  const cols = computeColumns(1280, 275, 380)
  assert.equal(cols.sidebar, 275)
  assert.equal(cols.rightbar, 380)
  assert.equal(cols.center, 625)
})
