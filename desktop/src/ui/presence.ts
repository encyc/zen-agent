/** Presence phases: the mount/unmount timing for surfaces that animate out.
 *
 * A component that unmounts the moment its `open` flag flips cannot play an
 * exit: React removes the node in the same commit that would start the
 * transition. So "still mounted" is decoupled from "is open" here — `exiting`
 * is mounted and painted with the closed target state, and only the leaving
 * timer reaches `closed` (unmount).
 *
 * Why a timer and not `animationend`: `styles/base.css` already collapses every
 * animation and transition to 0.01ms under `prefers-reduced-motion`, and an
 * event follows that collapse on a frame the caller cannot predict. One
 * `leavingMs` number is deterministic, testable, and lets the reduced-motion
 * path pass 0 to unmount immediately.
 *
 * Why transition and not @keyframes: a transition starts from the current
 * computed value, so an interrupted open/close reverses mid-flight instead of
 * jumping back to the keyframe's first stop.
 */

import { useEffect, useRef, useState } from 'react'

export type PresencePhase = 'closed' | 'entering' | 'open' | 'exiting'

/** Default leaving window for popovers and inline cards. */
export const PRESENCE_LEAVE_MS = 120

/** The leaving window to actually use.
 *
 * `styles/base.css` collapses every transition to 0.01ms under
 * `prefers-reduced-motion: reduce`, but a timer is not a transition: leaving it
 * at 120ms would strand an invisible, inert node in the DOM for the full window
 * on every close. Collapsing the window to 0 unmounts on the next commit, which
 * is what "no motion" means. */
export function effectiveLeaveMs(leavingMs: number, reducedMotion: boolean): number {
  return reducedMotion ? 0 : leavingMs
}

/** Props that take an exiting surface out of focus, AT and pointer input.
 *
 * `aria-hidden` alone would be wrong here: the node still holds focus while it
 * fades, and `aria-hidden` on a subtree containing the focused element is an
 * accessibility violation (and leaves it reachable by Tab). `inert` covers
 * focus, AT and pointer at once, which is exactly the claim the exit phase
 * makes. */
export function inertProps(closed: boolean): { inert?: '' } {
  return closed ? { inert: '' } : {}
}

/** The one transition table, literal to the design doc:

 *   closed   + open  → entering   (mount on this commit, paint closed target)
 *   closed   + !open → closed
 *   entering + open  → open       (settled; the hook's rAF is what gets here)
 *   entering + !open → exiting    (interrupted mid-enter)
 *   open     + open  → open
 *   open     + !open → exiting
 *   exiting  + open  → entering   (interrupted mid-exit, reverses)
 *   exiting  + !open → exiting
 *
 * `closed` is reachable only by the leaving timer in `usePresence` plus the
 * `closed + !open` identity — no flag flip unmounts a node mid-flight. */
export function nextPhase(phase: PresencePhase, wantOpen: boolean): PresencePhase {
  if (wantOpen) return phase === 'closed' || phase === 'exiting' ? 'entering' : 'open'
  return phase === 'closed' ? 'closed' : 'exiting'
}

/** Non-`closed` phases are in the DOM. */
export function isMounted(phase: PresencePhase): boolean {
  return phase !== 'closed'
}

/** What the node publishes as `data-state`, i.e. which CSS target state it is
 * transitioning toward. `exiting` is mounted but already paints as closed. */
export function presenceState(phase: PresencePhase): 'open' | 'closed' {
  return phase === 'entering' || phase === 'open' ? 'open' : 'closed'
}

/** Hand a caller's boolean to the phase machine.
 *
 * `leavingMs` is the exit window; pass 0 (or any value the caller computed from
 * `prefers-reduced-motion`) to unmount on the next commit. */
/** The reader's motion preference, tracked live (it can change mid-session from
 * the OS settings panel). */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return reduced
}

export function usePresence(
  open: boolean,
  leavingMs: number,
): { mounted: boolean; state: 'open' | 'closed' } {
  const [phase, setPhase] = useState<PresencePhase>('closed')
  // Read inside the effects below so a stale `open` never drives the phase.
  const wantRef = useRef(open)
  wantRef.current = open

  useEffect(() => {
    setPhase((current) => nextPhase(current, open))
  }, [open])

  // `entering` is one frame long: it exists so the first commit paints the
  // closed target state and the next commit can transition away from it.
  //
  // Both callbacks below re-derive from `wantRef` instead of writing the literal
  // phase: a close that lands in the same frame the opener's rAF was scheduled
  // for would otherwise overwrite `exiting` with `open` and strand the overlay
  // visibly open forever. Re-deriving from the live intent is what makes the
  // machine idempotent under a same-frame open/close race.
  useEffect(() => {
    if (phase !== 'entering') return undefined
    const frame = window.requestAnimationFrame(() => {
      setPhase((current) => nextPhase(current, wantRef.current))
    })
    return () => window.cancelAnimationFrame(frame)
  }, [phase])

  useEffect(() => {
    if (phase !== 'exiting') return undefined
    const timer = window.setTimeout(() => {
      setPhase((current) => (wantRef.current ? nextPhase(current, true) : 'closed'))
    }, leavingMs)
    return () => window.clearTimeout(timer)
  }, [phase, leavingMs])

  return { mounted: isMounted(phase), state: presenceState(phase) }
}

/** `usePresence` with the reduced-motion collapse applied, for callers that do
 * not care to make the choice themselves. */
export function useSurfacePresence(
  open: boolean,
  leavingMs: number = PRESENCE_LEAVE_MS,
): { mounted: boolean; state: 'open' | 'closed'; inert: { inert?: '' } } {
  const reduced = usePrefersReducedMotion()
  const presence = usePresence(open, effectiveLeaveMs(leavingMs, reduced))
  return { ...presence, inert: inertProps(presence.state === 'closed') }
}
