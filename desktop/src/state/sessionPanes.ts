/** The session pane registry: the coding-mode strip's state.
 *
 * One pane per open session, each with its own chat store (its own nodes,
 * paging cursor and usage rows), draft, and residency flag. A pane whose
 * session has a resident slot streams live off the broadcast frames (the
 * frame router routes by the frame's session tag through the sink below);
 * a pane whose slot is gone shows a static disk snapshot and offers to load
 * the session again. Prompts and commands are addressed to the pane's
 * session explicitly, so a background sibling keeps running untouched. */

import { create } from 'zustand'
import { createChatStore, useChatStore, type ChatStoreApi } from './chatStore.ts'
import {
  routeReply,
  setPaneSink,
  type FrameHandling,
} from './frameRouter.ts'
import { useAppStore } from './appStore.ts'
import { useComposerStore, useNoticeStore, useSessionStore, useSettingsStore } from './stores.ts'
import { TRANSCRIPT_PAGE_SIZE, olderPageCursor } from './transcriptWindow.ts'
import { translate } from '../i18n/messages.ts'
import type { AgentEventFrame, TranscriptResult, Usage } from '../rpc/types.ts'

/** Strip cap: the focused session plus up to two side panes — the host keeps
 * at most four resident slots, and a zen router or another project may hold
 * one of them. */
export const PANE_CAP = 3

export interface SessionPane {
  path: string
  title: string
  chat: ChatStoreApi
  /** A run owns this pane's session. */
  busy: boolean
  /** A snapshot has been painted (live or static). */
  loaded: boolean
  /** A resident slot serves this session: frames stream, submits go direct. */
  live: boolean
  draft: string
}

interface PanesState {
  panes: SessionPane[]
  open(path: string): Promise<void>
  close(path: string): void
  /** Focus a pane: it becomes the current session (the resume the sidebar
   * click does today) and a static pane upgrades to live. */
  focus(path: string): Promise<void>
  submit(path: string): Promise<void>
  cancel(path: string): Promise<void>
  loadOlder(path: string): Promise<boolean>
  setDraft(path: string, text: string): void
  finalizeAll(): void
}

const POLL_MS = 2500

let pollTimer: ReturnType<typeof setInterval> | null = null

function appHandling(): FrameHandling {
  return {
    get: () => useAppStore.getState(),
    set: (partial) => useAppStore.setState(partial),
  }
}

/** The session's display title from the sidebar list, else the file name. */
function titleFor(path: string): string {
  const session = useSessionStore
    .getState()
    .sessions.find((entry) => entry.path === path)
  return session?.title || session?.name || path.split('/').pop() || path
}

function notice(key: string): void {
  useNoticeStore.getState().show({
    kind: 'error',
    text: translate(useSettingsStore.getState().locale, key),
  })
}

export const usePanesStore = create<PanesState>((set, get) => {
  const patchPane = (path: string, patch: Partial<SessionPane>) =>
    set((state) => ({
      panes: state.panes.map((pane) =>
        pane.path === path ? { ...pane, ...patch } : pane,
      ),
    }))
  const withChat = (path: string, fn: (chat: ChatStoreApi) => void) => {
    const pane = get().panes.find((entry) => entry.path === path)
    if (pane !== undefined) fn(pane.chat)
  }
  const setBusy = (path: string, busy: boolean) => patchPane(path, { busy })

  /** The session snapshot: the slot's own transcript when it is resident
   * (validated by its session tag — an older runtime that ignored the tag
   * counts as the refusal path), else the disk read any session file allows.
   * A static pane still shows full content; only streaming needs the slot. */
  async function loadSnapshot(path: string): Promise<void> {
    const rpc = useAppStore.getState().client()
    try {
      const frame = await rpc.send<TranscriptResult & { session?: string }>({
        type: 'transcript',
        session: path,
        limit: TRANSCRIPT_PAGE_SIZE,
      })
      if (frame.type === 'result' && frame.ok && frame.session === path) {
        withChat(path, (chat) => chat.getState().loadSnapshot(frame))
        patchPane(path, { loaded: true, live: true })
        return
      }
    } catch {
      // not resident — fall through to the disk read
    }
    try {
      const project = useAppStore.getState().project
      const frame = await rpc.send({
        type: 'zen_fleet',
        op: 'transcript',
        path,
        limit: TRANSCRIPT_PAGE_SIZE,
        ...(project !== null ? { project } : {}),
      })
      if (frame.type === 'result' && frame.ok) {
        withChat(path, (chat) =>
          chat.getState().loadSnapshot(frame as unknown as TranscriptResult),
        )
        patchPane(path, { loaded: true, live: false })
        return
      }
    } catch {
      // no snapshot available; the pane stays empty with a load affordance
    }
    patchPane(path, { loaded: false, live: false })
  }

  function startPoll(): void {
    if (pollTimer !== null || typeof window === 'undefined') return
    pollTimer = setInterval(() => {
      void pollPanes()
    }, POLL_MS)
  }

  function stopPoll(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    panes: [],

    async open(path) {
      if (get().panes.some((pane) => pane.path === path)) {
        await get().focus(path)
        return
      }
      const current = useSessionStore.getState().currentPath
      // The strip always shows the session on screen next to the new pane:
      // seed it from the primary store (its pane, its busy state, the draft
      // the dock is about to stop carrying) so nothing repaints blank.
      if (get().panes.length === 0 && current !== null && current !== path) {
        const primary = useChatStore.getState()
        const chat = createChatStore()
        chat.getState().restorePane(primary.pane())
        set({
          panes: [
            {
              path: current,
              title: titleFor(current),
              chat,
              busy: useAppStore.getState().busy,
              loaded: primary.nodes.length > 0,
              live: true,
              draft: useComposerStore.getState().draft,
            },
          ],
        })
      }
      if (get().panes.length >= PANE_CAP) {
        notice('panes.capReached')
        return
      }
      set((state) => ({
        panes: [
          ...state.panes,
          { path, title: titleFor(path), chat: createChatStore(), busy: false, loaded: false, live: false, draft: '' },
        ],
      }))
      await loadSnapshot(path)
      startPoll()
    },

    close(path) {
      set((state) => ({ panes: state.panes.filter((pane) => pane.path !== path) }))
      if (get().panes.length === 0) stopPoll()
    },

    async focus(path) {
      if (useSessionStore.getState().currentPath !== path) {
        await useAppStore.getState().resumeSession(path)
      }
      if (get().panes.some((pane) => pane.path === path && !pane.live)) {
        await loadSnapshot(path)
      }
    },

    async submit(path) {
      const pane = get().panes.find((entry) => entry.path === path)
      if (pane === undefined) return
      const text = pane.draft.trim()
      if (text === '') return
      patchPane(path, { draft: '' })
      if (text.startsWith('/')) {
        await submitCommand(path, text)
        return
      }
      // A static pane's slot is gone: resume makes it resident (and current —
      // interaction focuses), then the tagged prompt reaches its own slot.
      if (!pane.live) await get().focus(path)
      setBusy(path, true)
      try {
        const frame = await useAppStore
          .getState()
          .client()
          .send({ type: 'prompt', text, session: path })
        routeReply(frame, appHandling())
      } catch {
        // transport-level failure: the exit listener already flagged it
        setBusy(path, false)
      }
    },

    async cancel(path) {
      try {
        await useAppStore
          .getState()
          .client()
          .send({ type: 'cancel', session: path })
      } catch {
        // the pane's own poll and events settle it either way
      }
      withChat(path, (chat) => chat.getState().finalize())
      setBusy(path, false)
    },

    async loadOlder(path) {
      const pane = get().panes.find((entry) => entry.path === path)
      if (pane === undefined) return false
      const chatState = pane.chat.getState()
      const before = olderPageCursor(chatState.hasMoreBefore, chatState.firstLoadedIndex)
      if (before === null) return false
      const rpc = useAppStore.getState().client()
      try {
        const frame = await rpc.send<TranscriptResult & { session?: string }>({
          type: 'transcript',
          session: path,
          limit: TRANSCRIPT_PAGE_SIZE,
          before,
        })
        if (frame.type === 'result' && frame.ok && frame.session === path) {
          pane.chat.getState().prependOlder(frame)
          return true
        }
      } catch {
        // fall through to the disk read
      }
      try {
        const project = useAppStore.getState().project
        const frame = await rpc.send({
          type: 'zen_fleet',
          op: 'transcript',
          path,
          before,
          ...(project !== null ? { project } : {}),
        })
        if (frame.type === 'result' && frame.ok) {
          pane.chat.getState().prependOlder(frame as unknown as TranscriptResult)
          return true
        }
      } catch {
        // nothing older is reachable right now
      }
      return false
    },

    setDraft(path, text) {
      patchPane(path, { draft: text })
    },

    finalizeAll() {
      for (const pane of get().panes) {
        pane.chat.getState().finalize()
      }
      set((state) => ({
        panes: state.panes.map((pane) => ({ ...pane, busy: false })),
      }))
    },
  }

  async function submitCommand(path: string, text: string): Promise<void> {
    const space = text.indexOf(' ')
    const name = space < 0 ? text.slice(1) : text.slice(1, space)
    const argument = space < 0 ? '' : text.slice(space + 1).trim()
    let frame: Record<string, unknown>
    try {
      frame = await useAppStore
        .getState()
        .client()
        .send({ type: 'command', name, argument, session: path })
    } catch {
      return
    }
    const ok = frame.type === 'result' && frame.ok !== false
    const message =
      frame.type === 'error'
        ? String(frame.message ?? 'command failed')
        : String(frame.reason ?? frame.message ?? 'command failed')
    withChat(path, (chat) =>
      chat.getState().pushCommand(name, argument, ok ? (frame.message as string | null) ?? null : message),
    )
    // /new, /fork, /resume, /retry and friends rebuild the transcript; the
    // pane refetches its own snapshot instead of the shell's.
    if (ok && frame.reset_transcript === true) {
      await loadSnapshot(path)
    }
  }
})

/** One status round for every live pane: the busy dot follows the slot, and
 * a reply that no longer names the pane's session means the slot is gone —
 * the pane goes static rather than showing someone else's run. */
export async function pollPanes(): Promise<void> {
  const panes = usePanesStore
    .getState()
    .panes.filter((pane) => pane.live && pane.loaded)
  if (panes.length === 0) return
  const rpc = useAppStore.getState().client()
  await Promise.all(
    panes.map(async (pane) => {
      try {
        const frame = await rpc.send<Record<string, unknown>>({
          type: 'status',
          session: pane.path,
        })
        if (
          frame.type === 'result' &&
          frame.ok === true &&
          frame.session === pane.path
        ) {
          usePanesStore.setState((state) => ({
            panes: state.panes.map((entry) =>
              entry.path === pane.path
                ? { ...entry, busy: frame.running === true }
                : entry,
            ),
          }))
          return
        }
      } catch {
        // the slot is unreachable: go static below
      }
      usePanesStore.setState((state) => ({
        panes: state.panes.map((entry) =>
          entry.path === pane.path ? { ...entry, live: false, busy: false } : entry,
        ),
      }))
    }),
  )
}

/** The registry is the frame router's pane sink, from module init: pane
 * events stream, usage rows settle, refusals become inline failure rows. */
setPaneSink({
  hasPane: (session) =>
    session !== null &&
    usePanesStore.getState().panes.some((pane) => pane.path === session),
  applyEvent: (session, event: AgentEventFrame) => {
    if (session === null) return
    const pane = usePanesStore.getState().panes.find((entry) => entry.path === session)
    if (pane === undefined) return
    pane.chat.getState().applyEvent(event)
    if (event.type === 'message_start' && event.message?.role === 'user') {
      usePanesStore.setState((state) => ({
        panes: state.panes.map((entry) =>
          entry.path === session ? { ...entry, busy: true } : entry,
        ),
      }))
    }
    if (event.type === 'agent_end' || event.type === 'turn_end') {
      usePanesStore.setState((state) => ({
        panes: state.panes.map((entry) =>
          entry.path === session ? { ...entry, busy: false } : entry,
        ),
      }))
    }
  },
  applyUsage: (session, usage: Usage | null, turnMs, termination) => {
    if (session === null) return
    const pane = usePanesStore.getState().panes.find((entry) => entry.path === session)
    if (pane === undefined) return
    pane.chat.getState().pushUsage(usage, turnMs, termination)
    pane.chat.getState().finalize()
    usePanesStore.setState((state) => ({
      panes: state.panes.map((entry) =>
        entry.path === session ? { ...entry, busy: false } : entry,
      ),
    }))
  },
  paneFailure: (session, text) => {
    if (session === null) return
    const pane = usePanesStore.getState().panes.find((entry) => entry.path === session)
    if (pane === undefined) return
    pane.chat.getState().pushFailure(text)
    usePanesStore.setState((state) => ({
      panes: state.panes.map((entry) =>
        entry.path === session ? { ...entry, busy: false } : entry,
      ),
    }))
  },
  sessionEnded: () => usePanesStore.getState().finalizeAll(),
})

/** Test seam: clear the strip and its timer between test files. */
export function resetPanesForTests(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  usePanesStore.setState({ panes: [] })
}
