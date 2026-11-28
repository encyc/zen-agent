/** The math behind the lightbox's wheel zoom and drag pan, pure so it can be
 * tested without a DOM. The image is laid out at its fit size and then moved
 * with `translate(offset) scale(s)` around its center, so a point q on the
 * image (relative to its center) sits at `center + offset + q * s`.
 *
 * Scale runs 1 (fit, the folded preview size) to 8. Offsets are clamped so
 * the zoomed picture never leaves its pane: the image may only move as far
 * as its own overflow at the current scale. */

export const MIN_SCALE = 1
export const MAX_SCALE = 8
/** Pixels of wheel delta per unit of exponential zoom; matches trackpad
 * pinches (which arrive as ctrl+wheel) and notched mouse wheels alike. */
const WHEEL_SENSITIVITY = 0.0015

export interface ZoomState {
  scale: number
  offset: { x: number; y: number }
}

interface Size {
  w: number
  h: number
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** Keep the picture covering its pane: half the overflow at each axis is the
 * farthest the center may drift. At scale 1 there is no overflow, so the
 * offset pins to zero — fit always means centered. */
export function clampOffset(state: ZoomState, viewport: Size, fit: Size): ZoomState {
  const maxX = Math.max(0, (fit.w * state.scale - viewport.w) / 2)
  const maxY = Math.max(0, (fit.h * state.scale - viewport.h) / 2)
  return {
    scale: state.scale,
    offset: {
      x: clamp(state.offset.x, -maxX, maxX),
      y: clamp(state.offset.y, -maxY, maxY),
    },
  }
}

/** One wheel tick: scale exponentially around the cursor, then re-clamp. The
 * point under the cursor stays under it, which is what makes zooming feel
 * aimed instead of pulsing from the middle. Saturated scales are a no-op. */
export function zoomAt(
  state: ZoomState,
  cursor: { x: number; y: number },
  wheelDelta: number,
  viewport: Size,
  fit: Size,
): ZoomState {
  const factor = Math.exp(-wheelDelta * WHEEL_SENSITIVITY)
  const scale = clamp(state.scale * factor, MIN_SCALE, MAX_SCALE)
  if (scale === state.scale) return state
  const ratio = scale / state.scale
  return clampOffset(
    {
      scale,
      offset: {
        x: cursor.x - (cursor.x - state.offset.x) * ratio,
        y: cursor.y - (cursor.y - state.offset.y) * ratio,
      },
    },
    viewport,
    fit,
  )
}

/** One drag step: translate by the pointer delta, then re-clamp. */
export function pan(
  state: ZoomState,
  delta: { x: number; y: number },
  viewport: Size,
  fit: Size,
): ZoomState {
  return clampOffset(
    {
      scale: state.scale,
      offset: { x: state.offset.x + delta.x, y: state.offset.y + delta.y },
    },
    viewport,
    fit,
  )
}

/** Back to fit, centered. */
export function resetZoom(): ZoomState {
  return { scale: MIN_SCALE, offset: { x: 0, y: 0 } }
}
