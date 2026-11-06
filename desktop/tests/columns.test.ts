import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CENTER_MIN,
  CHAT_WIDTH_DEFAULT,
  CHAT_WIDTH_MAX,
  CHAT_WIDTH_MIN,
  RIGHTBAR_DEFAULT,
  RIGHTBAR_MIN,
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  clampChatWidth,
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

test('pi work-panel floors: 450 center, 244 panel, 360 default', () => {
  assert.equal(CENTER_MIN, 450)
  assert.equal(RIGHTBAR_MIN, 244)
  assert.equal(RIGHTBAR_DEFAULT, 360)
  const cols = computeColumns(1280, 275, RIGHTBAR_DEFAULT)
  assert.equal(cols.rightbar, 360)
  assert.equal(cols.center, 645)
  // 900 - 275 - 450 = 175, below the 244 floor: the track drops entirely.
  assert.equal(computeColumns(900, 275, 360).rightbar, 0)
})

test('the conversation band clamps to the pi 560–900 range', () => {
  assert.equal(CHAT_WIDTH_DEFAULT, 760)
  assert.equal(CHAT_WIDTH_MIN, 560)
  assert.equal(CHAT_WIDTH_MAX, 900)
  assert.equal(clampChatWidth(500), 560)
  assert.equal(clampChatWidth(760), 760)
  assert.equal(clampChatWidth(1200), 900)
})
