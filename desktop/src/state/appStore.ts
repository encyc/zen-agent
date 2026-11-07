/** Orchestrator: owns the RpcClient, wires server frames into the stores, and
 * exposes every user action (prompt/steer/cancel/approve/session ops/menus).
 * The prompt/steer split mirrors the TUI composer: Enter while busy steers,
 * and Esc cancels. */

import { startFailureOf, type StartFailure } from './startFailure.ts'
import { create } from 'zustand'
import { homeDir } from '@tauri-apps/api/path'
import { RpcClient } from '../rpc/client'
import type {
  CompactionRecord,
  McpServerRow,
  Mode,
  SecretProviderRow,
  ServerFrame,
  SkillRow,
  StatusResult,
} from '../rpc/types'
import {
  useApprovalStore,
  useAskStore,
  useCatalogStore,
  useComposerStore,
  useMcpStore,
  useNoticeStore,
  useProjectStore,
  useSecretsStore,
  useSessionStore,
  useSettingsStore,
  useSkillsStore,
  useStatusStore,
} from './stores'
import { useChatStore } from './chatStore'
import { nextDraft } from './pending'
import { frameError } from '../rpc/frames'

export type ConnectionState = 'idle' | 'starting' | 'ready' | 'exited'

export type ExportFormat = 'md' | 'html'

export type McpLayer = 'global' | 'project'

export interface McpServerInput {
  name: string
  transport: 'stdio' | 'http'
  command?: string
  url?: string
  env?: Record<string, string>
  headers?: Record<string, string>
  layer: McpLayer
}

const LAST_CWD_KEY = 'zen.last.cwd'

/** First-launch host: the user's home folder, so the chat shell can start
 * without a project picker. `undefined` falls through to zen's own cwd. */
export async function defaultProjectCwd(): Promise<string | undefined> {
  try {
    const home = await homeDir()
    return home === '' ? undefined : home
  } catch {
    return undefined
  }
}

export function lastProjectCwd(): string {
  try {
    return localStorage.getItem(LAST_CWD_KEY) ?? ''
  } catch {
    return ''
  }
}

interface AppState {
  connection: ConnectionState
  exitCode: number | null
  stderrTail: string[]
  startFailure: StartFailure | null
  pid: number | null
  busy: boolean
  connect(bin?: string, cwd?: string, args?: string[]): Promise<void>
  disconnect(): Promise<void>
  client(): RpcClient
  refreshStatus(): Promise<void>
  refreshSessions(): Promise<void>
  refreshCatalog(): Promise<void>
  refreshSecrets(): Promise<void>
  saveSecret(name: string, value: string): Promise<Record<string, unknown>>
  clearSecret(name: string): Promise<Record<string, unknown>>
  testSecret(scope: 'model' | 'search', target: string): Promise<Record<string, unknown>>
  refreshSkills(): Promise<void>
  readSkill(name: string): Promise<Record<string, unknown>>
  rescanSkills(): Promise<Record<string, unknown>>
  toggleSkill(name: string, enabled: boolean): Promise<Record<string, unknown>>
  refreshMcp(): Promise<void>
  addMcpServer(input: McpServerInput): Promise<Record<string, unknown>>
  removeMcpServer(name: string, layer: McpLayer): Promise<Record<string, unknown>>
  testMcpServer(name: string): Promise<Record<string, unknown>>
  relocateProject(from: string, to: string): Promise<Record<string, unknown>>
  openProject(path: string): Promise<void>
  refreshTranscript(): Promise<void>
  submit(): Promise<void>
  steer(text: string): Promise<void>
  cancel(): Promise<void>
  approve(requestId: string, decision: 'allow' | 'always' | 'deny'): Promise<void>
  resolveAsk(requestId: string, answers: (string[] | null)[]): Promise<void>
  runCommand(name: string, argument: string): Promise<void>
  setModel(modelId: string): Promise<void>
  setMode(mode: Mode): Promise<void>
  setThinking(level: string): Promise<void>
  newSession(): Promise<void>
  forkSession(): Promise<void>
  openSession(path: string): Promise<void>
  resumeSession(path: string): Promise<void>
  deleteSession(path: string): Promise<void>
  renameSession(title: string): Promise<void>
  togglePin(pinned: boolean): Promise<void>
  importSession(path: string): Promise<boolean>
  exportSession(format: ExportFormat): Promise<string | null>
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
  startFailure: null,
  pid: null,
  busy: false,

  client() {
    if (client === null) client = new RpcClient()
    return client
  },

  async connect(bin, cwd, args) {
    const rpc = get().client()
    set({ connection: 'starting', exitCode: null, stderrTail: [], startFailure: null })
    useChatStore.getState().reset()
    useApprovalStore.getState().clear()
    useAskStore.getState().clear()

    if (!wired) {
      wired = true
      rpc.onFrame((frame: ServerFrame) => handleFrame(frame, { set, get }))
      rpc.onStderr((line) => {
        set((state) => ({ stderrTail: [...state.stderrTail.slice(-49), line] }))
      })
      rpc.onExit((code, pid) => {
        // Restarting at another project stops the old child, whose delayed
        // exit event must not end the replacement process the store tracks.
        const current = get().pid
        if (pid !== undefined && current !== null && pid !== current) return
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
      // The wire contract is versioned: refuse to talk to a server that does
      // not speak the same protocol (its result frames would mislead the UI).
      await rpc.handshake()
      try {
        localStorage.setItem(LAST_CWD_KEY, cwd ?? '')
      } catch {
        // ignore
      }
      if (cwd !== undefined && cwd !== '') useProjectStore.getState().addProject(cwd)
      set({ connection: 'ready', pid })
      await get().refreshTranscript()
      await get().refreshStatus()
      await get().refreshSessions()
      await get().refreshCatalog()
      pollTimer = setInterval(() => void get().refreshStatus(), 2000)
    } catch (error) {
      const failure = startFailureOf(error)
      set({ connection: 'exited', exitCode: null, startFailure: failure })
      set((state) => ({
        stderrTail: [
          ...state.stderrTail,
          failure === null ? String(error) : `failed to start ${failure.bin} rpc: ${failure.osError}`,
        ],
      }))
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
    // pi lists every project at once; the sidebar sections do the grouping.
    return get()
      .client()
      .send({ type: 'session', op: 'list', scope: 'all' })
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

  refreshSecrets() {
    return get()
      .client()
      .send({ type: 'secrets', op: 'list' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          useSecretsStore
            .getState()
            .setProviders(
              (frame.model_providers ?? []) as SecretProviderRow[],
              (frame.search_providers ?? []) as SecretProviderRow[],
            )
        }
      })
      .catch(() => {})
  },

  async saveSecret(name, value) {
    const frame = await get()
      .client()
      .send({ type: 'secrets', op: 'set', name, value })
    if (frame.type === 'result' && frame.ok) {
      await get().refreshSecrets()
      void get().refreshCatalog()
    }
    return frame
  },

  async clearSecret(name) {
    const frame = await get()
      .client()
      .send({ type: 'secrets', op: 'clear', name })
    if (frame.type === 'result' && frame.ok) {
      await get().refreshSecrets()
      void get().refreshCatalog()
    }
    return frame
  },

  testSecret(scope, target) {
    return get().client().send({ type: 'secrets', op: 'test', scope, target })
  },

  refreshSkills() {
    return get()
      .client()
      .send({ type: 'skills', op: 'list' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          useSkillsStore.getState().setSkills((frame.skills ?? []) as SkillRow[])
        }
      })
      .catch(() => {})
  },

  readSkill(name) {
    return get().client().send({ type: 'skills', op: 'read', name })
  },

  async rescanSkills() {
    const frame = await get().client().send({ type: 'skills', op: 'rescan' })
    if (frame.type === 'result' && frame.ok) {
      useSkillsStore.getState().setSkills((frame.skills ?? []) as SkillRow[])
    }
    return frame
  },

  async toggleSkill(name, enabled) {
    const frame = await get()
      .client()
      .send({ type: 'skills', op: 'toggle', name, enabled })
    if (frame.type === 'result' && frame.ok) await get().refreshSkills()
    return frame
  },

  refreshMcp() {
    return get()
      .client()
      .send({ type: 'mcp', op: 'list' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          useMcpStore
            .getState()
            .setServers(
              (frame.servers ?? []) as McpServerRow[],
              typeof frame.problem === 'string' ? frame.problem : null,
            )
        }
      })
      .catch(() => {})
  },

  async addMcpServer(input) {
    const frame = await get()
      .client()
      .send({ type: 'mcp', op: 'add', ...input })
    if (frame.type === 'result' && frame.ok) await get().refreshMcp()
    return frame
  },

  async removeMcpServer(name, layer) {
    const frame = await get()
      .client()
      .send({ type: 'mcp', op: 'remove', name, layer })
    if (frame.type === 'result' && frame.ok) await get().refreshMcp()
    return frame
  },

  testMcpServer(name) {
    return get().client().send({ type: 'mcp', op: 'test', name })
  },

  async relocateProject(from, to) {
    const frame = await get()
      .client()
      .send({ type: 'project', op: 'relocate', from, to })
    if (frame.type === 'result' && frame.ok) await get().refreshSessions()
    return frame
  },

  async openProject(path) {
    if (path === '' || lastProjectCwd() === path) return
    await get().disconnect()
    await get().connect(undefined, path)
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

  async resolveAsk(requestId, answers) {
    try {
      await get().client().send({ type: 'ask', request_id: requestId, answers })
    } catch {
      // The card is dropped either way: the ask tool blocks the run, so a
      // request that never reached the server has no second chance.
    }
    useAskStore.getState().pop(requestId)
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
    // A skill turn projects its own card from the streamed message.
    if (!name.startsWith('skill:')) {
      useChatStore.getState().pushCommand(name, argument, message)
    }
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
    await runSessionOp(get, { type: 'session', op: 'new' }, () => refreshAfterSessionChange(get))
  },

  async forkSession() {
    await runSessionOp(get, { type: 'session', op: 'fork' }, () => refreshAfterSessionChange(get))
  },

  async openSession(path) {
    const session = useSessionStore.getState().sessions.find((entry) => entry.path === path)
    const root = session?.project_path ?? ''
    // The runtime is cwd-bound. A session recorded in another project needs
    // the process restarted there before it can be resumed (pi does the same
    // through selectSession → activateProject).
    if (root !== '' && lastProjectCwd() !== root) {
      await get().disconnect()
      await get().connect(undefined, root)
    }
    await get().resumeSession(path)
  },

  async resumeSession(path) {
    await runSessionOp(get, { type: 'session', op: 'resume', path }, () =>
      refreshAfterSessionChange(get),
    )
  },

  async deleteSession(path) {
    await runSessionOp(get, { type: 'session', op: 'delete', path }, () =>
      refreshAfterSessionChange(get),
    )
  },

  async renameSession(title) {
    await runSessionCommand(get, 'rename', title)
    await get().refreshSessions()
  },

  async togglePin(pinned) {
    await runSessionCommand(get, 'pin', pinned ? 'off' : 'on')
    await get().refreshSessions()
  },

  async importSession(path) {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'import', path })
    } catch (error) {
      showActionError(error)
      return false
    }
    const error = frameError(frame)
    if (error !== null) {
      showActionError(error)
      return false
    }
    await refreshAfterSessionChange(get)
    return true
  },

  async exportSession(format) {
    const source = useSessionStore.getState().currentPath
    if (source === null) {
      showActionError('no current session to export')
      return null
    }
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'export', path: source, format })
    } catch (error) {
      showActionError(error)
      return null
    }
    const error = frameError(frame)
    if (error !== null) {
      showActionError(error)
      return null
    }
    return typeof frame.path === 'string' ? frame.path : null
  },

  async promotePending(id) {
    await runSessionOp(
      get,
      { type: 'session', op: 'promote', pending_id: id },
      () => get().refreshStatus(),
    )
  },

  async dequeuePending() {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'session', op: 'dequeue' })
    } catch (error) {
      showActionError(error)
      return
    }
    const error = frameError(frame)
    if (error !== null) {
      showActionError(error)
      return
    }
    if (frame.dequeued === true && typeof frame.text === 'string') {
      const composer = useComposerStore.getState()
      composer.setDraft(nextDraft(composer.draft, frame.text))
    }
    await get().refreshStatus()
  },

  async clearPending() {
    await runSessionOp(get, { type: 'session', op: 'clear_pending' }, () =>
      get().refreshStatus(),
    )
  },

  async compactNow() {
    await runSessionOp(get, { type: 'session', op: 'compact' }, async () => {
      await get().refreshTranscript()
      await get().refreshStatus()
    })
  },

  async undoLast() {
    await runSessionOp(get, { type: 'session', op: 'undo' }, () =>
      refreshAfterSessionChange(get),
    )
  },
}))

async function runSessionOp(
  get: () => AppState,
  payload: Record<string, unknown>,
  after: () => Promise<void> | void,
): Promise<void> {
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send(payload)
  } catch (error) {
    showActionError(error)
    return
  }
  const error = frameError(frame)
  if (error !== null) {
    showActionError(error)
    return
  }
  await after()
}

async function runSessionCommand(
  get: () => AppState,
  name: string,
  argument: string,
): Promise<void> {
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send({ type: 'command', name, argument })
  } catch (error) {
    showActionError(error)
    return
  }
  const error = frameError(frame)
  if (error !== null) {
    showActionError(error)
  }
}

function showActionError(value: unknown): void {
  useNoticeStore.getState().show({
    kind: 'error',
    text: value instanceof Error ? value.message : String(value),
  })
}

async function refreshAfterSessionChange(get: () => AppState): Promise<void> {
  await get().refreshTranscript()
  await get().refreshStatus()
  await get().refreshSessions()
}

function handleFrame(frame: ServerFrame, handling: FrameHandling): void {
  if (frame.type === 'event') {
    const event = frame.event
    if (event.type === 'ask_request' && event.request_id) {
      useAskStore.getState().push({
        requestId: event.request_id,
        questions: event.questions ?? [],
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
