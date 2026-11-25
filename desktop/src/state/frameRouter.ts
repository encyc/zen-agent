/** The frame router: every server frame meets the stores here.
 *
 * Extracted from `appStore.ts` (that file may only shrink under the
 * architecture budget) and extended with pane routing: frames tagged with a
 * session that has an open pane land in that pane's chat store instead of
 * being dropped, so a background session streams live next to the one on
 * screen. The per-session usage baselines moved here with the routing —
 * `refreshStatus` records them, prompt results settle deltas from them. */

import type { RpcClient } from '../rpc/client.ts'
import {
  frameBelongsTo,
  frameErrorNotice,
  frameSessionBelongsTo,
  type ReplyLike,
} from '../rpc/frames.ts'
import type {
  AgentEventFrame,
  ServerFrame,
  Usage,
} from '../rpc/types.ts'
import { useChatStore } from './chatStore.ts'
import {
  useApprovalStore,
  useAskStore,
  useNoticeStore,
  useSessionStore,
  useSettingsStore,
} from './stores.ts'
import { applyFleetFrame } from './zenActions.ts'
import { applySwarmFrame } from './swarmWiring.ts'

/** The slice of the app store the router needs — appStore supplies it from
 * its own state; frameRouter never imports appStore (that would close a
 * cycle: appStore imports this module). */
export interface FrameHandling {
  get(): {
    project: string | null
    refreshStatus(): Promise<void>
    refreshSessions(): Promise<void>
  }
  set(partial: { busy?: boolean }): void
}

/** Where pane-addressed frames go. The pane registry registers itself at
 * module init; a null sink (tests, no panes yet) keeps today's behavior. */
export interface PaneSink {
  hasPane(session: string | null): boolean
  applyEvent(session: string | null, event: AgentEventFrame): void
  /** The settled turn: the usage delta since the last snapshot of this
   * session, its wall time and termination. */
  applyUsage(
    session: string | null,
    usage: Usage | null,
    turnMs: number | null,
    termination: string | null,
    detail: string | null,
  ): void
  /** A refusal addressed to this pane (a busy slot, a dead session). */
  paneFailure(session: string | null, text: string): void
  /** The sidecar died: settle every open pane. */
  sessionEnded(): void
}

let sink: PaneSink | null = null

export function setPaneSink(next: PaneSink | null): void {
  sink = next
}

export function getPaneSink(): PaneSink | null {
  return sink
}

/** Last seen cumulative usage per session, for computing a turn's delta row.
 * Sessions of one project run in parallel, so one global baseline would read
 * the other session's totals. */
const usageBaselines = new Map<string, Usage>()

export function recordUsageBaseline(session: string | null, usage: Usage | undefined): void {
  if (usage !== undefined) usageBaselines.set(session ?? '', usage)
}

function takeUsageDelta(session: string | null, total: Usage): Usage {
  const key = session ?? ''
  const previous = usageBaselines.get(key)
  const delta = previous === undefined
    ? total
    : {
        input: Math.max(0, total.input - previous.input),
        output: Math.max(0, total.output - previous.output),
        cache_read: Math.max(0, total.cache_read - previous.cache_read),
        cache_write: Math.max(0, total.cache_write - previous.cache_write),
        cost: Math.max(0, total.cost - previous.cost),
      }
  usageBaselines.set(key, total)
  return delta
}

/** The client the frame handlers are attached to: connect() may run again
 * after an exit (same client, same wiring — re-registering would duplicate
 * every node in the chat), while a fresh client (a new window, tests) wires
 * from scratch. */
let wiredClient: RpcClient | null = null

/** Register the frame stream for this client and name its session provider,
 * so every session-scoped request carries the on-screen session and the host
 * routes it to the right slot. */
export function ensureFrameWiring(rpc: RpcClient, handling: FrameHandling): void {
  if (wiredClient === rpc) return
  wiredClient = rpc
  rpc.setSessionProvider(() => useSessionStore.getState().currentPath)
  // Attach the bridge listeners even when nothing will call `start` (the
  // adopt path): broadcast frames reach the handlers only through them.
  void rpc.ensureListening().catch(() => {
    // A bridge without the event system (node tests) simply has no stream.
  })
  rpc.onFrame((frame: ServerFrame) => handleFrame(frame, handling))
}

/** A server refusal goes through the code→dictionary mapper (`message` stays
 * the fallback for codes this build does not know); returns false when the
 * frame is not a refusal. */
export function showFrameError(frame: ReplyLike): boolean {
  const text = frameErrorNotice(frame, useSettingsStore.getState().locale)
  if (text === null) return false
  useNoticeStore.getState().show({ kind: 'error', text })
  return true
}

export function handleFrame(frame: ServerFrame, handling: FrameHandling): void {
  const tagged = frame as { project?: unknown; session?: unknown }
  const project =
    typeof tagged.project === 'string' && tagged.project !== '' ? tagged.project : null
  const session =
    typeof tagged.session === 'string' && tagged.session !== '' ? tagged.session : null
  if (frame.type === 'event') {
    const event = frame.event
    // Prompts are answered by prefix-routed ids; a background slot's question
    // must stay in the queue even while another project or session is on
    // screen.
    if (event.type === 'ask_request' && event.request_id) {
      useAskStore.getState().push({
        requestId: event.request_id,
        questions: event.questions ?? [],
        project,
        session,
      })
      return
    }
    if (event.type === 'ask_cancel' && event.request_id) {
      useAskStore.getState().pop(event.request_id)
      return
    }
    if (event.type === 'approval_request' && event.request_id) {
      useApprovalStore.getState().push({
        requestId: event.request_id,
        toolName: event.tool_name ?? '?',
        args: event.args ?? '',
        argsTruncated: event.argsTruncated ?? false,
        risk: event.risk ?? null,
        target: event.target ?? null,
        reason: event.reason ?? null,
        project,
        session,
      })
      return
    }
    // Chat events from a background slot or session belong to its own pane,
    // which is refreshed from its snapshot when the person switches back.
    if (!frameBelongsTo(frame, handling.get().project)) return
    // An open pane streams its own session live, whatever is on screen.
    if (sink !== null && sink.hasPane(session)) {
      sink.applyEvent(session, event)
      if (event.type === 'agent_end' || event.type === 'turn_end') {
        void handling.get().refreshSessions()
      }
      return
    }
    if (!frameSessionBelongsTo(frame, useSessionStore.getState().currentPath)) {
      // A background session still changes the sidebar (titles, activity).
      if (event.type === 'agent_end' || event.type === 'turn_end') {
        void handling.get().refreshSessions()
      }
      return
    }
    useChatStore.getState().applyEvent(event)
    if (event.type === 'agent_end' || event.type === 'turn_end') {
      void handling.get().refreshStatus()
    }
    return
  }
  if (!frameBelongsTo(frame, handling.get().project)) return
  // Swarm rooms are per project: only the active project's room frames and
  // @you notices may reach the screen. Fleet frames carry a session tag of
  // the router slot — both are consumed before pane routing could misread
  // them as chat.
  if (applySwarmFrame(frame, handling.get().project)) return
  if (applyFleetFrame(frame)) return
  routeReply(frame as Record<string, unknown>, handling)
}

/** Where one direct reply (a `send` answer, not a broadcast frame) lands:
 * an open pane's store, the on-screen transcript, or — when the reply is for
 * a session the person has already left — just the refreshes. The same rules
 * the broadcast path applies. */
export function routeReply(frame: Record<string, unknown>, handling: FrameHandling): void {
  const tagged = frame as { session?: unknown }
  const session =
    typeof tagged.session === 'string' && tagged.session !== '' ? tagged.session : null
  if (sink !== null && sink.hasPane(session)) {
    handlePaneFrame(frame as ServerFrame, session)
    return
  }
  if (!frameSessionBelongsTo(frame, useSessionStore.getState().currentPath)) {
    void handling.get().refreshStatus()
    void handling.get().refreshSessions()
    return
  }
  handleResultFrame(frame, handling)
}

/** Result and error frames addressed to an open pane: usage rows settle into
 * the pane, refusals become inline failure rows — the banner stays for the
 * session the person is actually looking at. */
function handlePaneFrame(frame: ServerFrame, session: string | null): void {
  if (sink === null) return
  if (frame.type === 'error') {
    const message = typeof frame.message === 'string' ? frame.message : String(frame.message ?? '')
    sink.paneFailure(session, message)
    return
  }
  if (frame.type !== 'result') return
  // A turn that ended badly (a provider error, a guard kill) still spent
  // tokens: settle its row — the termination and its detail explain the ending.
  if ('termination' in frame && 'usage' in frame) {
    const delta = takeUsageDelta(session, frame.usage as Usage)
    sink.applyUsage(
      session,
      delta,
      null,
      (frame.termination as string | null) ?? null,
      (frame.detail as string | undefined) ?? null,
    )
  }
}

export function handleResultFrame(frame: Record<string, unknown>, handling: FrameHandling): void {
  // A prompt failure crosses as an error frame with a code; the banner
  // localizes it instead of dropping it (the raw detail stays in the log).
  if (frame.type === 'error') {
    showFrameError(frame)
    void handling.get().refreshStatus()
    return
  }
  if (frame.type !== 'result') {
    void handling.get().refreshStatus()
    return
  }
  // A prompt result closes a turn: land its usage row (the delta since the
  // previous snapshot — the wire usage is session-cumulative) and refresh.
  // ``ok`` is false for a failed or guard-killed turn, but it still spent
  // tokens: the row is where its termination and detail become visible.
  if ('termination' in frame && 'usage' in frame) {
    const total = frame.usage as Usage
    const key = typeof frame.session === 'string' ? frame.session : ''
    const delta = takeUsageDelta(key, total)
    useChatStore.getState().pushUsage(
      delta,
      null,
      (frame.termination as string | null) ?? null,
      (frame.detail as string | undefined) ?? null,
    )
    useChatStore.getState().finalize()
    void handling.get().refreshStatus()
    void handling.get().refreshSessions()
    return
  }
  void handling.get().refreshStatus()
}
