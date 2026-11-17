/** The detached session window's boot.
 *
 * A `session-*` webview renders the same coding surface with one session
 * pinned; it rides the sidecar the main window already started (a second
 * runtime would fork session state), so its boot adopts the running process
 * — `rpc_alive` → handshake — and only spawns when there is nothing to adopt
 * (this window beat the main one, or the runtime died). The project comes
 * from the query override, not hello's active slot; the session is opened in
 * the background (a sibling slot for a busy project, the exact holder
 * otherwise) so the main window's active session keeps running untouched.
 *
 * Deliberately not `appStore.connect`: the boot never touches the shared
 * `LAST_CWD_KEY` (the main window's default project must survive), never
 * resets the approval/ask queues (they are the shell's, and this window may
 * boot after the person already answered some), and never seeds panes. */

import { useAppStore } from './appStore.ts'
import { useChatStore } from './chatStore.ts'
import { ensureFrameWiring, getPaneSink, type FrameHandling } from './frameRouter.ts'
import { restorePrefs } from './prefsReplay.ts'
import { useSessionStore } from './stores.ts'
import type { BootOverride } from './bootMode.ts'

const POLL_MS = 2000
let pollTimer: ReturnType<typeof setInterval> | null = null
let wired = false

function handling(): FrameHandling {
  return {
    get: () => useAppStore.getState(),
    set: (partial) => useAppStore.setState(partial),
  }
}

export async function bootSessionWindow(override: BootOverride): Promise<void> {
  const rpc = useAppStore.getState().client()
  if (!wired) {
    wired = true
    ensureFrameWiring(rpc, handling())
    // Per-window connection lifecycle: the exit clears this window's poll,
    // settles the transcript and flips to the exit screen. The pid guard
    // keeps a replaced child's late event from ending the wrong boot.
    rpc.onStderr((line) => {
      useAppStore.setState((state) => ({
        stderrTail: [...state.stderrTail.slice(-49), line],
      }))
    })
    rpc.onExit((code, pid) => {
      const current = useAppStore.getState().pid
      if (pid !== undefined && current !== null && pid !== current) return
      if (pollTimer !== null) {
        clearInterval(pollTimer)
        pollTimer = null
      }
      useChatStore.getState().finalize()
      getPaneSink()?.sessionEnded()
      useAppStore.setState({ connection: 'exited', exitCode: code, busy: false })
    })
  }
  useAppStore.setState({ connection: 'starting', exitCode: null, startFailure: null })

  try {
    if (!(await rpc.alive())) {
      const pid = await rpc.start({ cwd: override.project ?? undefined })
      useAppStore.setState({ pid })
    }
    await rpc.handshake()

    // Pin the query's project: hello's active slot may belong to another
    // window's project. `project open` is host-level and background, so the
    // main window's active session keeps the foreground.
    const target = override.project !== null && override.project !== '' ? override.project : rpc.activeProject()
    if (target === null || target === '') {
      useAppStore.setState({ connection: 'exited' })
      return
    }
    const frame = await rpc.send<Record<string, unknown>>({
      type: 'project',
      op: 'open',
      root: target,
      resume: override.session,
      background: true,
    })
    if (frame.type !== 'result' || frame.ok !== true) {
      useAppStore.setState({ connection: 'exited' })
      return
    }
    const root = typeof frame.root === 'string' && frame.root !== '' ? frame.root : target
    rpc.setProject(root)
    useAppStore.setState({ project: root })

    // The slot may hold another session of this project: follow up with an
    // explicit resume, which loads in place (idle) or spawns a sibling (busy)
    // — the run in the other session never aborts.
    if (frame.session !== override.session) {
      const answer = await rpc.send({ type: 'session', op: 'resume', path: override.session })
      if (!(answer.type === 'result' && answer.ok === true)) {
        useAppStore.setState({ connection: 'exited' })
        return
      }
    }
    useSessionStore.getState().setCurrent(override.session)
    useAppStore.setState({ connection: 'ready' })

    // The transcript request is session-tagged by the client (current =
    // override.session), so it answers this session's slot.
    await useAppStore.getState().refreshTranscript()
    await useAppStore.getState().refreshStatus()
    void useAppStore.getState().refreshSessions()
    await restorePrefs(() => useAppStore.getState())

    // The poll rides the webview's event loop; node tests boot without one.
    if (pollTimer === null && typeof window !== 'undefined') {
      pollTimer = setInterval(() => {
        void useAppStore.getState().refreshStatus()
      }, POLL_MS)
    }
  } catch {
    useAppStore.setState({ connection: 'exited' })
  }
}

