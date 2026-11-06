/** Column solve — port of dsh ui-layout columns.ts. The right column shrinks,
 * then loses its track, before the center drops below its minimum. */

export interface Columns {
  sidebar: number
  center: number
  rightbar: number
}

export const CENTER_MIN = 450
export const SIDEBAR_MIN = 240
export const SIDEBAR_MAX = 520
export const SIDEBAR_DEFAULT = 275

/** pi's clamp: min(520px, calc(100vw - 320px)), never below the drag floor. */
export function sidebarMax(viewport: number): number {
  return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, viewport - 320))
}
export const SIDEBAR_COLLAPSED = 56
export const SIDEBAR_AUTO_COLLAPSE = 1024
export const RIGHTBAR_MIN = 244
export const RIGHTBAR_DEFAULT = 360
export const RIGHTBAR_MAX_RATIO = 0.7
export const RIGHTBAR_DEFAULT_RATIO = 0.45

export const CHAT_WIDTH_MIN = 560
export const CHAT_WIDTH_MAX = 900
export const CHAT_WIDTH_DEFAULT = 760

export function clampChatWidth(px: number): number {
  return Number.isFinite(px) ? clampWidth(px, CHAT_WIDTH_MIN, CHAT_WIDTH_MAX) : CHAT_WIDTH_DEFAULT
}

export function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(px)))
}

export function computeColumns(
  viewport: number,
  sidebar: number,
  rightbar: number,
  collapsedWidth = SIDEBAR_COLLAPSED,
): Columns {
  const s = sidebar === 0 ? collapsedWidth : clampWidth(sidebar, SIDEBAR_MIN, sidebarMax(viewport))
  const available = viewport - s - CENTER_MIN
  const r =
    rightbar === 0 || available < RIGHTBAR_MIN
      ? 0
      : Math.min(available, clampWidth(rightbar, RIGHTBAR_MIN, viewport * RIGHTBAR_MAX_RATIO))
  return { sidebar: s, center: Math.max(0, viewport - s - r), rightbar: r }
}
