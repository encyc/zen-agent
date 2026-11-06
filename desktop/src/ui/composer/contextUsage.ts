import type { ContextUsageDisplay } from '../../state/stores'

export interface ContextMeterView {
  /** Ring fill, 0–1, matching the leading figure. */
  ratio: number
  percent: number
}

/** Remaining is the default so the ring answers "how much room is left";
 * capacity colors stay on the used ratio either way. */
export function contextMeterView(
  usedRatio: number,
  mode: ContextUsageDisplay,
): ContextMeterView {
  const ratio = mode === 'used' ? usedRatio : 1 - usedRatio
  return { ratio, percent: Math.round(ratio * 100) }
}
