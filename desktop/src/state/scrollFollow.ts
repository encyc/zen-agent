/** Transcript scroll ownership: whether the view follows the tail, whether the
 * jump pill shows, and how many user turns landed since the reader left.
 *
 * A pure module on purpose. The rules are the easy part to get subtly wrong —
 * a programmatic scroll must not read as a user gesture, content growth must not
 * count as scrolling up, and the pill has two thresholds so it cannot flicker —
 * and each of those is one assertion here instead of one e2e flake.
 *
 * The mode mirrors the three states shadcn's message-scroller names, minus the
 * anchored case this app does not need: `scrollToRow` already releases follow,
 * so a jump target never fights the stream. */

/** Distance from the bottom (px) that still counts as "at the tail". Sub-pixel
 * tolerance so fractional scrollTop on HiDPI does not flip the state. */
export const SCROLL_EDGE = 8

/** Distance from the bottom (px) at which the jump pill appears. The gap to
 * `SCROLL_EDGE` is what keeps the pill from strobing near the tail. */
export const SCROLL_JUMP_THRESHOLD = 160

/** Both thresholds above are on `distance`, so this is only used to compare two
 * scrollTop samples for "did the reader actually move up". */
const SCROLL_POSITION_EPSILON = 0.5

export type ScrollMode = 'following' | 'free'

export interface ScrollSnapshot {
  mode: ScrollMode
  showJump: boolean
  atTop: boolean
  /** User turns that arrived while the reader was away from the tail. */
  unseen: number
}

export interface ScrollInput {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
  /** The previous sample, to tell a real upward scroll from a content growth. */
  lastScrollTop: number
  /** True when this component issued the scroll (jump pill, minimap, anchor
   * restore). Those move scrollTop without the reader leaving the tail. */
  programmatic: boolean
}

/** One scroll observation folded into the next snapshot.
 *
 * Order matters: the tail test wins over everything (reaching the bottom always
 * re-follows), and the "scrolled up" test requires an actual decrease so a
 * taller document cannot release follow on its own. */
export function nextScrollSnapshot(prev: ScrollSnapshot, input: ScrollInput): ScrollSnapshot {
  const distance = input.scrollHeight - input.scrollTop - input.clientHeight
  const atTop = input.scrollTop <= SCROLL_EDGE

  if (input.programmatic) {
    // A jump in flight: the position changed but the reader's intent did not.
    // Publish only what describes the geometry, never a mode change.
    return { ...prev, atTop }
  }

  if (distance <= SCROLL_EDGE) {
    return { mode: 'following', showJump: false, atTop, unseen: 0 }
  }

  const scrolledUp = input.scrollTop < input.lastScrollTop - SCROLL_POSITION_EPSILON
  if (!scrolledUp && prev.mode === 'following') {
    // Content grew below the fold; the reader is still at the tail they were
    // at, and the follow effect is about to scroll to the new bottom.
    return { ...prev, atTop }
  }

  // Away from the tail. Past the pill threshold show it; between the two
  // thresholds keep whatever it was, so a wheel tick at 158px does not blink.
  const showJump = distance > SCROLL_JUMP_THRESHOLD ? true : prev.showJump
  return { ...prev, mode: 'free', showJump, atTop }
}

/** User turns whose id is not in `seenIds`. Counting by id, not by index, is
 * what survives the mounted-history window: rows above the window are dropped
 * from the array, so an index-based count would re-count them as new. */
export function countUnseen(
  nodes: ReadonlyArray<{ kind: string; id: string }>,
  seenIds: ReadonlySet<string>,
): number {
  let unseen = 0
  for (const node of nodes) {
    if (node.kind === 'user' && !seenIds.has(node.id)) unseen += 1
  }
  return unseen
}
