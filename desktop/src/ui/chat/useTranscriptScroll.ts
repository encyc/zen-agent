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
  PROGRAMMATIC_FLIGHT_MS,
  countUnseen,
  nextScrollSnapshot,
  programmaticActive,
  type ProgrammaticFlight,
  type ScrollSnapshot,
} from '../../state/scrollFollow'
import type { ChatNode } from '../../state/chatStore'

const START: ScrollSnapshot = { mode: 'following', showJump: false, atTop: false, unseen: 0 }

export interface TranscriptScroll {
  /** Latest snapshot. `ref` is the same value for listeners that must not
   * re-bind on every sample. */
  snap: ScrollSnapshot
  snapRef: React.MutableRefObject<ScrollSnapshot>
  /** Flag the scroll this component is about to issue as programmatic.
   * `target` is where it should land (null: an instantaneous jump). */
  markProgrammatic(target?: number | null): void
  /** Fold one observation in (the listener calls this; effects may too). */
  observe(): void
  /** Show `next` immediately (a jump the view already decided on). */
  publish(next: ScrollSnapshot): void
  /** Attach to the scrolling element. A callback ref, not a plain one: the
   * listener has to bind when the node appears, and a `useEffect` reading
   * `ref.current` would run once while it is still null and never again. */
  attach(node: HTMLDivElement | null): void
}

export function useTranscriptScroll({
  scrollerRef,
  nodes,
  transcriptKey,
  onAtTop,
  onFollowing,
  onActiveChange,
}: {
  scrollerRef: React.MutableRefObject<HTMLDivElement | null>
  /** The loaded nodes, for the unseen count. */
  nodes: ReadonlyArray<ChatNode>
  /** Identity of the transcript being shown (the session path). A reused
   * component instance must not carry one session's seen set into the next. */
  transcriptKey: string | null
  /** The reader reached the top of the mounted window. */
  onAtTop(): void
  /** The reader is (back) at the tail: the caller shrinks its window. */
  onFollowing(): void
  /** The row under the reading line changed, for the minimap. */
  onActiveChange(id: string | null): void
}): TranscriptScroll {
  const [snap, setSnap] = useState<ScrollSnapshot>(START)
  /** The scrolling element, in state so the listener effect re-runs when it
   * mounts (a ref assignment alone is invisible to React). */
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const attach = useCallback(
    (node: HTMLDivElement | null) => {
      scrollerRef.current = node
      setScroller(node)
    },
    [scrollerRef],
  )
  const snapRef = useRef<ScrollSnapshot>(snap)
  /** scrollTop of the previous observation, to tell a real upward scroll from a
   * taller document. Mutable: the listener is passive and must not re-bind. */
  const lastTop = useRef(0)
  /** Set while this component moves the scroll itself (jump, minimap, anchor
   * restore): those events must not read as a user gesture. Spans a whole
   * smooth flight, not one frame — see PROGRAMMATIC_FLIGHT_MS. */
  const programmatic = useRef<ProgrammaticFlight | null>(null)
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

  const markProgrammatic = useCallback(
    (target?: number | null) => {
      // The sample the flight starts from must not count as a move either.
      lastTop.current = scrollerRef.current?.scrollTop ?? lastTop.current
      if (target === undefined) {
        // An instantaneous assignment moves scrollTop for one frame. Giving it
        // the smooth deadline instead would swallow the reader's own next
        // gesture — and the tail pin on mount runs through here on every session.
        programmatic.current = { target: null, until: Date.now() + 16 }
        window.requestAnimationFrame(() => {
          programmatic.current = null
        })
        return
      }
      programmatic.current = { target, until: Date.now() + PROGRAMMATIC_FLIGHT_MS }
    },
    [scrollerRef],
  )

  const observe = useCallback(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const isProgrammatic = programmaticActive(programmatic.current, Date.now(), scroller.scrollTop)
    // Landing ends the flight; the deadline is only the backstop.
    if (isProgrammatic && programmatic.current?.target !== null) programmatic.current = null
    const nextSnap = nextScrollSnapshot(snapRef.current, {
      scrollTop: scroller.scrollTop,
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
      lastScrollTop: lastTop.current,
      programmatic: isProgrammatic,
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
    if (scroller === null) return undefined
    scroller.addEventListener('scroll', observe, { passive: true })
    return () => scroller.removeEventListener('scroll', observe)
  }, [observe, scroller])

  // A transcript swap resets everything the previous one established: its seen
  // ids are meaningless here and its unseen count belongs to its session.
  useEffect(() => {
    seenIds.current = new Set()
    activeRef.current = null
    lastTop.current = 0
    programmatic.current = null
    publish(START)
  }, [transcriptKey, publish])

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

  return { snap, snapRef, markProgrammatic, observe, publish, attach }
}
