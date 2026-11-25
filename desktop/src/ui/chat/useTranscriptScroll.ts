/** Scroll ownership for one transcript scroller: the snapshot, the listener that
 * feeds it, the programmatic-scroll flag, and the unseen-turn bookkeeping.
 *
 * Extracted from ChatView so the rules ("did the reader leave the tail", "did a
 * user turn land below the fold") sit in one place and the view keeps only
 * layout. The decisions themselves are pure and tested in
 * `state/scrollFollow.ts`; this hook is the DOM half — refs, listeners, the
 * frame that clears the programmatic flag.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  countUnseen,
  nextScrollSnapshot,
  type ScrollSnapshot,
} from '../../state/scrollFollow'
import type { ChatNode } from '../../state/chatStore'

const START: ScrollSnapshot = { mode: 'following', showJump: false, atTop: false, unseen: 0 }

export interface TranscriptScroll {
  /** Latest snapshot. `ref` is the same value for listeners that must not
   * re-bind on every sample. */
  snap: ScrollSnapshot
  snapRef: React.MutableRefObject<ScrollSnapshot>
  /** Flag the scroll this component is about to issue as programmatic. */
  markProgrammatic(): void
  /** Fold one observation in (the listener calls this; effects may too). */
  observe(): void
  /** Show `next` immediately (a jump the view already decided on). */
  publish(next: ScrollSnapshot): void
}

export function useTranscriptScroll({
  scrollerRef,
  nodes,
  onAtTop,
  onFollowing,
  onActiveChange,
}: {
  scrollerRef: React.RefObject<HTMLDivElement | null>
  /** The loaded nodes, for the unseen count. */
  nodes: ReadonlyArray<ChatNode>
  /** The reader reached the top of the mounted window. */
  onAtTop(): void
  /** The reader is (back) at the tail: the caller shrinks its window. */
  onFollowing(): void
  /** The row under the reading line changed, for the minimap. */
  onActiveChange(id: string | null): void
}): TranscriptScroll {
  const [snap, setSnap] = useState<ScrollSnapshot>(START)
  const snapRef = useRef<ScrollSnapshot>(snap)
  /** scrollTop of the previous observation, to tell a real upward scroll from a
   * taller document. Mutable: the listener is passive and must not re-bind. */
  const lastTop = useRef(0)
  /** Set while this component moves the scroll itself (jump, minimap, anchor
   * restore): that event must not read as a user gesture. */
  const programmatic = useRef(false)
  /** User turns already seen at the tail, by id — a shrinking mounted window
   * would make an index-based count re-count old rows as new. */
  const seenIds = useRef<Set<string>>(new Set())
  const activeRef = useRef<string | null>(null)

  // The callbacks are recreated every render by the view; keep them in a ref so
  // the passive listener binds once per scroller instead of once per render.
  const handlers = useRef({ onAtTop, onFollowing, onActiveChange })
  handlers.current = { onAtTop, onFollowing, onActiveChange }

  const publish = useCallback((next: ScrollSnapshot) => {
    snapRef.current = next
    setSnap(next)
  }, [])

  const markProgrammatic = useCallback(() => {
    programmatic.current = true
    lastTop.current = scrollerRef.current?.scrollTop ?? lastTop.current
    window.requestAnimationFrame(() => {
      programmatic.current = false
    })
  }, [scrollerRef])

  const observe = useCallback(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const nextSnap = nextScrollSnapshot(snapRef.current, {
      scrollTop: scroller.scrollTop,
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
      lastScrollTop: lastTop.current,
      programmatic: programmatic.current,
    })
    lastTop.current = scroller.scrollTop

    // The reading line sits 30% down the viewport; the last user row above it
    // is the turn the rail highlights.
    const line = scroller.getBoundingClientRect().top + scroller.clientHeight * 0.3
    let active: string | null = null
    scroller.querySelectorAll<HTMLElement>('[data-minimap-kind="user"]').forEach((row) => {
      if (row.getBoundingClientRect().top <= line) active = row.dataset.minimapId ?? null
    })
    if (active !== activeRef.current) {
      activeRef.current = active
      handlers.current.onActiveChange(active)
    }

    publish(nextSnap)
    if (nextSnap.atTop) handlers.current.onAtTop()
    if (nextSnap.mode === 'following') handlers.current.onFollowing()
  }, [publish, scrollerRef])

  useEffect(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return undefined
    scroller.addEventListener('scroll', observe, { passive: true })
    return () => scroller.removeEventListener('scroll', observe)
  }, [observe, scrollerRef])

  // Arriving content: at the tail it is pinned (and everything is seen); away
  // from it, only USER turns that landed below the fold are news — the
  // assistant's own deltas must not move the count.
  useEffect(() => {
    const current = snapRef.current
    if (current.mode === 'following') {
      for (const node of nodes) if (node.kind === 'user') seenIds.current.add(node.id)
      if (current.unseen !== 0) publish({ ...current, unseen: 0 })
      return
    }
    const unseen = countUnseen(nodes, seenIds.current)
    if (unseen !== current.unseen) publish({ ...current, unseen })
  }, [nodes, publish])

  return { snap, snapRef, markProgrammatic, observe, publish }
}
