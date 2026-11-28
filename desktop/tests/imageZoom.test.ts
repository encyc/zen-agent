/** Wheel-zoom and drag-pan math for the image lightbox: zoom keeps the point
 * under the cursor, pan and zoom both stay clamped to the pane. Run with
 * `node --test tests/imageZoom.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { clampOffset, pan, resetZoom, zoomAt } from '../src/ui/chat/imageZoom.ts'

const VIEWPORT = { w: 400, h: 300 }
// 2x landscape picture: at scale 1 it fits, at scale 2 it overflows x only.
const FIT = { w: 400, h: 200 }

test('zoom in around the cursor keeps that point put', () => {
  // Both axes overflow (fit is 2x the viewport), so no clamping interferes
  // and the invariant can hold exactly.
  const start = { scale: 2, offset: { x: 100, y: 50 } }
  const cursor = { x: -80, y: 20 }
  const next = zoomAt(start, cursor, -50, VIEWPORT, FIT)

  // The image point under the cursor (relative to center) is unchanged.
  const q = { x: (cursor.x - start.offset.x) / start.scale, y: (cursor.y - start.offset.y) / start.scale }
  const coveredX = next.offset.x + q.x * next.scale
  const coveredY = next.offset.y + q.y * next.scale
  assert.equal(coveredX, cursor.x)
  assert.equal(coveredY, cursor.y)
  assert.ok(next.scale > start.scale)
})

test('an axis without overflow trades the cursor point for no gaps', () => {
  // The landscape fit overflows x only; zooming near a corner keeps the
  // horizontal aim but pins the short axis to center instead of showing
  // empty bands above and below the picture.
  const start = { scale: 1, offset: { x: 0, y: 0 } }
  const next = zoomAt(start, { x: 100, y: -50 }, -100, VIEWPORT, FIT)
  assert.equal(next.offset.y, 0)
  assert.ok(next.offset.x !== 0)
})

test('zoom out saturates at the fit scale and stays centered', () => {
  const zoomed = { scale: 2, offset: { x: 50, y: 0 } }
  const next = zoomAt(zoomed, { x: 0, y: 0 }, 100_000, VIEWPORT, FIT)
  assert.deepEqual(next, { scale: 1, offset: { x: 0, y: 0 } })
})

test('zoom in saturates at the max scale', () => {
  const start = { scale: 7.9, offset: { x: 0, y: 0 } }
  const next = zoomAt(start, { x: 0, y: 0 }, -100_000, VIEWPORT, FIT)
  assert.equal(next.scale, 8)
})

test('a saturated zoom is a no-op, not a drift', () => {
  const fit = { scale: 1, offset: { x: 0, y: 0 } }
  const next = zoomAt(fit, { x: 120, y: 0 }, 500, VIEWPORT, FIT)
  assert.deepEqual(next, fit)
})

test('pan never lets the picture leave the pane', () => {
  const zoomed = { scale: 2, offset: { x: 0, y: 0 } }
  // 2x of 400 wide in a 400 pane: overflow 400, so ±200 is the limit.
  const far = pan(zoomed, { x: 10_000, y: -10_000 }, VIEWPORT, FIT)
  assert.equal(far.offset.x, 200)
  // No vertical overflow at 2x (200*2 = 400 in 300... wait: 400 > 300 → ±50).
  assert.equal(far.offset.y, -50)
})

test('pan at fit scale pins to the center', () => {
  const next = pan({ scale: 1, offset: { x: 99, y: 99 } }, { x: 10, y: 10 }, VIEWPORT, FIT)
  assert.deepEqual(next, { scale: 1, offset: { x: 0, y: 0 } })
})

test('shifting vertical overflow clamps per axis', () => {
  const state = clampOffset(
    { scale: 2, offset: { x: 10_000, y: 10_000 } },
    VIEWPORT,
    FIT,
  )
  assert.equal(state.offset.x, 200)
  assert.equal(state.offset.y, 50)
})

test('reset returns to fit, centered', () => {
  assert.deepEqual(resetZoom(), { scale: 1, offset: { x: 0, y: 0 } })
})
