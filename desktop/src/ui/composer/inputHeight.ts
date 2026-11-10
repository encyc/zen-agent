/** Drag-resize limits for the composer textarea. The floor is two text lines
 * plus the textarea's own vertical padding; the ceiling keeps the expanded
 * plate inside the window. Pure so node --test can cover the clamps. */

export const INPUT_MIN_LINES = 2
export const INPUT_MAX_VIEWPORT_RATIO = 0.6
const DEFAULT_LINE_HEIGHT = 24

export interface InputHeightLimits {
  min: number
  max: number
  step: number
}

export function inputHeightLimits(
  lineHeight: number,
  padding: number,
  viewport: number,
): InputHeightLimits {
  const step =
    Number.isFinite(lineHeight) && lineHeight > 0 ? lineHeight : DEFAULT_LINE_HEIGHT
  const pad = Number.isFinite(padding) ? Math.max(0, padding) : 0
  const min = Math.ceil(step * INPUT_MIN_LINES + pad)
  const height = Number.isFinite(viewport) ? viewport : 0
  return { min, max: Math.max(min, Math.round(height * INPUT_MAX_VIEWPORT_RATIO)), step }
}

export function clampInputHeight(px: number, limits: InputHeightLimits): number {
  return Math.min(limits.max, Math.max(limits.min, Math.round(px)))
}
