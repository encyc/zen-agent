/** Orchestrator: owns the RpcClient, wires server frames into the stores, and
 * exposes every user action (prompt/steer/cancel/approve/session ops/menus).
 * The prompt/steer split mirrors the TUI composer: Enter while busy steers,
 * and Esc cancels. */

import { create } from 'zustand'
import { RpcClient } from '../rpc/client'
import type { CompactionRecord, Mode, ServerFrame, StatusResult } from '../rpc/types'
import {
  useApprovalStore,
  useCatalogStore,
  useComposerStore,
  useSessionStore,
  useSettingsStore,
  useStatusStore,
} from './stores'
import { useChatStore } from './chatStore'
import { nextDraft } from './pending'

export type ConnectionState = 'idle' | 'starting' | 'ready' | 'exited'

interface AppState {
  connection: ConnectionState
  exitCode: number | null
  stderrTail: string[]
  pid: number | null
  busy: boolean
  connect(bin?: string, cwd?: string, args?: string[]): Promise<void>
  disconnect(): Promise<void>
  client(): RpcClient
  refreshStatus(): Promise<void>
  refreshSessions(): Promise<void>
  refreshCatalog(): Promise<void>
  refreshTranscript(): Promise<void>
  submit(): Promise<void>
  steer(text: string): Promise<void>
  cancel(): Promise<void>
  approve(requestId: string, decision: 'allow' | 'always' | 'deny'): Promise<void>
  runCommand(name: string, argument: string): Promise<void>
  setModel(modelId: string): Promise<void>
  setMode(mode: Mode): Promise<void>
  setThinking(level: string): Promise<void>
  newSession(): Promise<void>
  forkSession(): Promise<void>
  resumeSession(path: string): Promise<void>
  deleteSession(path: string): Promise<void>
  promotePending(id: number): Promise<void>
  dequeuePending(): Promise<void>
  clearPending(): Promise<void>
  compactNow(): Promise<void>
  undoLast(): Promise<void>
}

let client: RpcClient | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let statusInFlight = false
/** Frame listeners register once per process: connect() may run again after
 * an exit, and stacked handlers would duplicate every node in the chat. */
let wired = false
/** Last seen cumulative usage, for computing a turn's delta row. */
let lastUsage: { input: number; output: number; cache_read: number; cache_write: number; cost: number } | null = null

interface FrameHandling {
  set: (partial: Partial<Pick<AppState, 'busy'>>) => void
  get: () => AppState
}

export const useAppStore = create<AppState>((set, get) => ({
  connection: 'idle',
  exitCode: null,
  stderrTail: [],
  pid: null,
  busy: false,

  client() {
    if (client === null) client = new RpcClient()
    return client
  },

  async connect(bin, cwd, args) {
    const rpc = get().client()
    set({ connection: 'starting', exitCode: null, stderrTail: [] })
    useChatStore.getState().reset()
    useApprovalStore.getState().clear()

    if (!wired) {
      wired = true
      rpc.onFrame((frame: ServerFrame) => handleFrame(frame, { set, get }))
      rpc.onStderr((line) => {
        set((state) => ({ stderrTail: [...state.stderrTail.slice(-49), line] }))
      })
      rpc.onExit((code) => {
        if (pollTimer !== null) {
          clearInterval(pollTimer)
          pollTimer = null
        }
        useChatStore.getState().finalize()
        set({ connection: 'exited', exitCode: code, busy: false })
      })
    }

    try {
      // `--mode ask` keeps old released zen binaries happy (they crash without
      // an explicit mode) and is the safe default; the mode chip switches later.
      const pid = await rpc.start({ bin, cwd, args: args ?? ['--mode', 'ask', '--no-mcp'] })
      set({ connection: 'ready', pid })
      await get().refreshTranscript()
      await get().refreshStatus()
      await get().refreshSessions()
      await get().refreshCatalog()
      pollTimer = setInterval(() => void get().refreshStatus(), 2000)
    } catch (error) {
      set({ connection: 'exited', exitCode: null })
      set((state) => ({ stderrTail: [...state.stderrTail, String(error)] }))
    }
  },

  async disconnect() {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    await get().client().stop()
    set({ connection: 'exited', busy: false })
  },

  refreshStatus() {
    if (statusInFlight) return Promise.resolve()
    statusInFlight = true
    return get()
      .client()
      .send({ type: 'status' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          const status = frame as unknown as StatusResult
          useStatusStore.getState().set(status)
          set({ busy: Boolean(status.running) })
          if (status.usage !== undefined) lastUsage = status.usage
        }
      })
      .catch(() => {})
      .finally(() => {
        statusInFlight = false
      })
  },

  refreshSessions() {
    return get()
      .client()
      .send({ type: 'session', op: 'list' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          const sessions = (frame.sessions ?? []) as { path: string; current: boolean }[]
          useSessionStore.getState().setSessions(sessions as never)
          const current = sessions.find((session) => session.current)
          useSessionStore.getState().setCurrent(current?.path ?? null)
        }
      })
      .catch(() => {})
  },

  refreshCatalog() {
    const rpc = get().client()
    void rpc
      .send({ type: 'models' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          useCatalogStore.getState().setModels((frame.models ?? []) as never)
        }
      })
      .catch(() => {})
    void rpc
      .send({ type: 'commands' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          useCatalogStore.getState().setCommands((frame.commands ?? []) as never)
        }
      })
      .catch(() => {})
    return Promise.resolve()
  },

  refreshTranscript() {
    return get()
      .client()
      .send({ type: 'transcript' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          const transcript = (frame.transcript ?? []) as never
          const notices = (frame.notices ?? []) as never
          const compactions = (frame.compactions ?? []) as CompactionRecord[]
          useChatStore.getState().loadSnapshot({
            type: 'result',
            id: '',
            ok: true,
            transcript,
            notices,
            compactions,
          })
        }
      })
      .catch(() => {})
  },

  async submit() {
    const { draft, images, setDraft, clearImages } = useComposerStore.getState()
    const text = draft.trim()
    if (!text && images.length === 0) return
    if (text.startsWith('/')) {
      const space = text.indexOf(' ')
      const name = space < 0 ? text.slice(1) : text.slice(1, space)
      const argument = space < 0 ? '' : text.slice(space + 1).trim()
      setDraft('')
      await get().runCommand(name, argument)
      return
    }
    setDraft('')
    clearImages()
    const payload: Record<string, unknown> = { type: 'prompt', text }
    if (images.length > 0) payload.images = images.map((image) => image.path)
    try {
      const frame = await get().client().send(payload)
      handleResultFrame(frame, { set, get })
    } catch {
      // transport-level failure: the exit listener already flagged it
    }
  },

  async steer(text) {
    const trimmed = text.trim()
    if (!trimmed) return
    useComposerStore.getState().setDraft('')
    try {
      await get().client().send({ type: 'steer', text: trimmed })
    } catch {
      // ignore
    }
    void get().refreshStatus()
  },

  async cancel() {
    try {
      await get().client().send({ type: 'cancel' })
    } catch {
      // ignore
    }
    useChatStore.getState().finalize()
    void get().refreshStatus()
  },

  async approve(requestId, decision) {
    try {
      await get().client().send({ type: 'approval', request_id: requestId, decision })
    } catch {
      // ignore
    }
    useApprovalStore.getState().pop(requestId)
    void get().refreshStatus()
  },

  async runCommand(name, argument) {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'command', name, argument })
    } catch {
      return
    }
    handleResultFrame(frame, { set, get })
    if (frame.type !== 'result' || !frame.ok) {
      const message =
        frame.type === 'error' ? String(frame.message ?? 'command failed') : String(frame.reason ?? 'command failed')
      useChatStore.getState().pushCommand(name, argument, message)
      return
    }
    const message = typeof frame.message === 'string' ? frame.message : null
    useChatStore.getState().pushCommand(name, argument, message)
    if (frame.appearance === 'dark' || frame.appearance === 'light' || frame.appearance === 'auto') {
      useSettingsStore.getState().setTheme(frame.appearance)
    }
    if (frame.language === 'zh' || frame.language === 'en') {
      useSettingsStore.getState().setLocale(frame.language)
    }
    if (typeof frame.model === 'string' && frame.model) {
      void get().refreshStatus()
      void get().refreshCatalog()
    }
    if (frame.quit === true) {
      set({ busy: false })
    }
    // /compact, /new, /fork, /resume, /retry, /handoff and /import rebuild the
    // transcript or switch files; the chat store has no event journal for it.
    if (frame.reset_transcript === true) {
      await get().refreshTranscript()
      await get().refreshSessions()
    }
    void get().refreshStatus()
  },

  async setModel(modelId) {
    try {
      await get().client().send({ type: 'model', model_id: modelId })
    } catch {
      return
    }
    void get().refreshStatus()
    void get().refreshCatalog()
  },

  async setMode(mode) {
    try {
      await get().client().send({ type: 'mode', mode })
    } catch {
      return
    }
    void get().refreshStatus()
  },

  async setThinking(level) {
    await get().runCommand('thinking', level)
  },

  async newSession() {
    try {
      await get().client().send({ type: 'session', op: 'new' })
    } catch {
      return
    }
    await refreshAfterSessionChange(get)
  },

  async forkSession() {
    try {
      await get().client().send({ type: 'session', op: 'fork' })
    } catch {
      return
    }
    await refreshAfterSessionChange(get)
  },

  async resumeSession(path) {
    try {
      await get().client().send({ type: 'session', op: 'resume', path })
    } catch {
      return
    }
    await refreshAfterSessionChange(get)
  },

  async deleteSession(path) {
    try {
      await get().client().send({ type: 'session', op: 'delete', path })
    } catch {
      return
    }
    await refreshAfterSessionChange(get)
  },

  async promotePending(id) {
    try {
      await get().client().send({ type: 'session', op: 'promote', pending_id: id })
    } catch {
      return
    }
    await get().refreshStatus()
  },

  async dequeuePending() {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'dequeue' })
    } catch {
      return
    }
    if (
      frame.type === 'result' &&
      frame.ok &&
      frame.dequeued === true &&
      typeof frame.text === 'string'
    ) {
      const composer = useComposerStore.getState()
      composer.setDraft(nextDraft(composer.draft, frame.text))
    }
    await get().refreshStatus()
  },

  async clearPending() {
    try {
      await get().client().send({ type: 'session', op: 'clear_pending' })
    } catch {
      return
    }
    await get().refreshStatus()
  },

  async compactNow() {
    try {
      await get().client().send({ type: 'session', op: 'compact' })
    } catch {
      return
    }
    await get().refreshTranscript()
    await get().refreshStatus()
  },

  async undoLast() {
    try {
      await get().client().send({ type: 'session', op: 'undo' })
    } catch {
      return
    }
    await refreshAfterSessionChange(get)
  },
}))

async function refreshAfterSessionChange(get: () => AppState): Promise<void> {
  await get().refreshTranscript()
  await get().refreshStatus()
  await get().refreshSessions()
}

function handleFrame(frame: ServerFrame, handling: FrameHandling): void {
  if (frame.type === 'event') {
    const event = frame.event
    if (event.type === 'approval_request' && event.request_id) {
      useApprovalStore.getState().push({
        requestId: event.request_id,
        toolName: event.tool_name ?? '?',
        args: event.args ?? '',
        risk: event.risk ?? null,
        target: event.target ?? null,
        reason: event.reason ?? null,
      })
      return
    }
    useChatStore.getState().applyEvent(event)
    if (event.type === 'agent_end' || event.type === 'turn_end') {
      void handling.get().refreshStatus()
    }
    return
  }
  handleResultFrame(frame as Record<string, unknown>, handling)
}

function handleResultFrame(frame: Record<string, unknown>, handling: FrameHandling): void {
  if (frame.type === 'error') return
  if (frame.type !== 'result' || !frame.ok) {
    void handling.get().refreshStatus()
    return
  }
  // A prompt result closes a turn: land its usage row (the delta since the
  // previous snapshot — the wire usage is session-cumulative) and refresh.
  if ('termination' in frame && 'usage' in frame) {
    const total = frame.usage as {
      input: number
      output: number
      cache_read: number
      cache_write: number
      cost: number
    }
    const delta = lastUsage === null
      ? total
      : {
          input: Math.max(0, total.input - lastUsage.input),
          output: Math.max(0, total.output - lastUsage.output),
          cache_read: Math.max(0, total.cache_read - lastUsage.cache_read),
          cache_write: Math.max(0, total.cache_write - lastUsage.cache_write),
          cost: Math.max(0, total.cost - lastUsage.cost),
        }
    lastUsage = total
    useChatStore.getState().pushUsage(
      delta,
      null,
      (frame.termination as string | null) ?? null,
    )
    useChatStore.getState().finalize()
    void handling.get().refreshStatus()
    void handling.get().refreshSessions()
    return
  }
  void handling.get().refreshStatus()
}
