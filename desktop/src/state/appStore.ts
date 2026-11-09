/** Orchestrator: owns the RpcClient, wires server frames into the stores, and
 * exposes every user action (prompt/steer/cancel/approve/session ops/menus).
 * The prompt/steer split mirrors the TUI composer: Enter while busy steers,
 * and Esc cancels. */

import { startFailureOf, type StartFailure } from './startFailure.ts'
import { create } from 'zustand'
import { homeDir } from '@tauri-apps/api/path'
import { TRANSCRIPT_PAGE_SIZE, olderPageCursor } from './transcriptWindow'
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
  useWorkPanelStore,
} from './stores'
import { useChatStore } from './chatStore'
import { nextDraft } from './pending'
import { loadPrefs, replayFrames, savePref, spawnArgs } from './prefs'
import { frameErrorNotice, type ReplyLike } from '../rpc/frames'
import { reportRefreshFailure } from './refresh'

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
  /** Set while a project switch is replacing the runtime; the shell stays
   * mounted so the switch reads as a content swap, not a reload. */
  switching: { path: string } | null
  /** Where the ☯ toggle came from: the session and mode to restore on exit.
   * Taiji is a session + a mode default (spec D2/D5), not a runtime mode. */
  taijiReturn: { path: string; mode: Mode } | null
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
  fetchLimits(): Promise<Record<string, unknown>>
  setLimit(key: string, value: number | null): Promise<Record<string, unknown>>
  resetLimit(key: string): Promise<Record<string, unknown>>
  readSkill(name: string): Promise<Record<string, unknown>>
  rescanSkills(): Promise<Record<string, unknown>>
  toggleSkill(name: string, enabled: boolean): Promise<Record<string, unknown>>
  refreshMcp(): Promise<void>
  addMcpServer(input: McpServerInput): Promise<Record<string, unknown>>
  removeMcpServer(name: string, layer: McpLayer): Promise<Record<string, unknown>>
  testMcpServer(name: string): Promise<Record<string, unknown>>
  relocateProject(from: string, to: string): Promise<Record<string, unknown>>
  openProject(path: string): Promise<void>
  refreshTranscript(): Promise<boolean>
  loadOlderTranscript(): Promise<boolean>
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
  enterTaiji(): Promise<void>
  exitTaiji(): Promise<void>
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
/** One older-page fetch at a time: the scroll handler can fire repeatedly. */
let olderInFlight = false
/** Frame listeners register once per process: connect() may run again after
 * an exit, and stacked handlers would duplicate every node in the chat. */
let wired = false
/** Last seen cumulative usage, for computing a turn's delta row. */
let lastUsage: { input: number; output: number; cache_read: number; cache_write: number; cost: number } | null = null

interface FrameHandling {
  set: (partial: Partial<Pick<AppState, 'busy'>>) => void
  get: () => AppState
}

interface BootOptions {
  bin?: string
  cwd?: string
  args?: string[]
  /** A switch must not leave the previous project's transcript on screen if it fails to load. */
  fromSwitch: boolean
}

/** The one boot sequence both connect and openProject ride: start the child,
 * pin its pid, handshake, remember the project, load the transcript and
 * replay the person's saved choices. Callers own the connection state. */
async function bootChild(
  set: (partial: Partial<AppState>) => void,
  get: () => AppState,
  opts: BootOptions,
): Promise<void> {
  const rpc = get().client()
  // `--mode ask` keeps old released zen binaries happy and is the safe
  // default; the user's own mode is replayed from prefs (see restorePrefs).
  const pid = await rpc.start({ bin: opts.bin, cwd: opts.cwd, args: opts.args })
  set({ pid })
  await rpc.handshake()
  try {
    localStorage.setItem(LAST_CWD_KEY, opts.cwd ?? '')
  } catch {
    // ignore
  }
  if (opts.cwd !== undefined && opts.cwd !== '') useProjectStore.getState().addProject(opts.cwd)
  // The three boot reads are independent requests, id-correlated on the wire:
  // run them together so the first paint waits for the slowest, not the sum.
  const [loaded] = await Promise.all([
    get().refreshTranscript(),
    restorePrefs(get),
    get().refreshSessions(),
  ])
  if (!loaded && opts.fromSwitch) useChatStore.getState().reset()
}

export const useAppStore = create<AppState>((set, get) => ({
  connection: 'idle',
  switching: null,
  taijiReturn: null,
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
    set({
      connection: 'starting',
      switching: null,
      exitCode: null,
      stderrTail: [],
      startFailure: null,
    })
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
        set({ connection: 'exited', exitCode: code, busy: false, switching: null })
      })
    }

    try {
      await bootChild(set, get, { bin, cwd, args: args ?? spawnArgs(), fromSwitch: false })
      set({ connection: 'ready' })
      await get().refreshCatalog()
      pollTimer = setInterval(() => {
        // A switch owns the wire while it runs; polling it would race the
        // replacement's own refresh with stale answers.
        if (get().switching === null) void get().refreshStatus()
      }, 2000)
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
      .send<StatusResult>({ type: 'status' })
      .then((status) => {
        if (status.type === 'result' && status.ok) {
          useStatusStore.getState().set(status)
          set({ busy: Boolean(status.running) })
          if (status.usage !== undefined) lastUsage = status.usage
        }
      })
      .catch((error) => reportRefreshFailure('status', error))
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
      .catch((error) => reportRefreshFailure('sessions', error))
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
      .catch((error) => reportRefreshFailure('models', error))
    void rpc
      .send({ type: 'commands' })
      .then((frame) => {
        if (frame.type === 'result' && frame.ok) {
          useCatalogStore.getState().setCommands((frame.commands ?? []) as never)
        }
      })
      .catch((error) => reportRefreshFailure('commands', error))
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
      .catch((error) => reportRefreshFailure('secrets', error))
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
      .catch((error) => reportRefreshFailure('skills', error))
  },

  fetchLimits() {
    return get().client().send({ type: 'limits', op: 'get' })
  },

  setLimit(key, value) {
    return get().client().send({ type: 'limits', op: 'set', key, value })
  },

  resetLimit(key) {
    return get().client().send({ type: 'limits', op: 'reset', key })
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
      .catch((error) => reportRefreshFailure('mcp', error))
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
    if (path === '') return
    if (path === lastProjectCwd() && get().connection === 'ready') return
    // Nothing to preserve before the first connection: a plain start.
    if (get().connection !== 'ready') {
      await get().connect(undefined, path)
      return
    }
    if (get().switching !== null) return

    // The runtime is cwd-bound, so a project switch replaces the child. The
    // difference from connect() is what the user sees: the shell stays
    // mounted, the old transcript keeps painting until the new snapshot
    // lands, and the whole swap is a progress bar instead of a boot screen.
    set({ switching: { path }, busy: false })
    useApprovalStore.getState().clear()
    useAskStore.getState().clear()
    useChatStore.getState().finalize()

    try {
      await bootChild(set, get, { cwd: path, args: spawnArgs(), fromSwitch: true })
      set({ connection: 'ready', switching: null, exitCode: null, startFailure: null })
    } catch (error) {
      const failure = startFailureOf(error)
      set({ connection: 'exited', switching: null, startFailure: failure, exitCode: null })
      set((state) => ({
        stderrTail: [
          ...state.stderrTail,
          failure === null ? String(error) : `failed to start ${failure.bin} rpc: ${failure.osError}`,
        ],
      }))
    }
  },

  async refreshTranscript() {
    try {
      // First load is the tail only: a long session paints fast, and the
      // reader pages backwards when they scroll to the top.
      const frame = await get().client().send({
        type: 'transcript',
        limit: TRANSCRIPT_PAGE_SIZE,
      })
      if (frame.type !== 'result' || !frame.ok) return false
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
        hasMoreBefore: frame.hasMoreBefore === true,
      })
      return true
    } catch {
      return false
    }
  },

  async loadOlderTranscript() {
    if (olderInFlight) return false
    const chat = useChatStore.getState()
    const before = olderPageCursor(chat.hasMoreBefore, chat.loadedViews)
    if (before === null) return false
    olderInFlight = true
    try {
      const frame = await get().client().send({
        type: 'transcript',
        limit: TRANSCRIPT_PAGE_SIZE,
        before,
      })
      if (frame.type !== 'result' || !frame.ok) return false
      useChatStore.getState().prependOlder({
        type: 'result',
        id: '',
        ok: true,
        transcript: (frame.transcript ?? []) as never,
        notices: [],
        compactions: [],
        hasMoreBefore: frame.hasMoreBefore === true,
      })
      return true
    } catch {
      return false
    } finally {
      olderInFlight = false
    }
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
    if (frame.screen === 'memory-manager') {
      useWorkPanelStore.getState().showTab('memory')
    }
    // A skill turn projects its own card from the streamed message.
    if (!name.startsWith('skill:') && frame.screen !== 'memory-manager') {
      useChatStore.getState().pushCommand(name, argument, message)
    }
    if (frame.appearance === 'dark' || frame.appearance === 'light' || frame.appearance === 'auto') {
      useSettingsStore.getState().setTheme(frame.appearance)
    }
    if (frame.language === 'zh' || frame.language === 'en') {
      useSettingsStore.getState().setLocale(frame.language)
    }
    if (typeof frame.model === 'string' && frame.model) {
      // The server is the authority on which model is live; remember it so the
      // next child starts there.
      savePref('model', frame.model)
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
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'model', model_id: modelId })
    } catch {
      return
    }
    if (frame.type === 'result' && frame.ok !== false) savePref('model', modelId)
    void get().refreshStatus()
    void get().refreshCatalog()
  },

  async setMode(mode) {
    let frame: Record<string, unknown>
    try {
      frame = await get().client().send({ type: 'mode', mode })
    } catch {
      return
    }
    if (frame.type === 'result' && frame.ok !== false) savePref('mode', mode)
    void get().refreshStatus()
  },

  async setThinking(level) {
    savePref('thinking', level)
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
    // through selectSession → activateProject). openProject keeps the shell
    // mounted while it happens.
    if (root !== '' && lastProjectCwd() !== root) await get().openProject(root)
    await get().resumeSession(path)
  },

  async resumeSession(path) {
    await runSessionOp(get, { type: 'session', op: 'resume', path }, () =>
      refreshAfterSessionChange(get),
    )
  },

  async enterTaiji() {
    // The taiji session is a project singleton the host creates at boot;
    // a host without it (a linked worktree) simply has no ☯ chip.
    const session = useSessionStore
      .getState()
      .sessions.find((entry) => entry.name === 'taiji')
    if (session === undefined) return
    const current = useSessionStore.getState().currentPath
    if (current !== session.path && get().taijiReturn === null) {
      set({
        taijiReturn: {
          path: current ?? '',
          mode: useStatusStore.getState().status?.mode ?? 'zen',
        },
      })
    }
    if (session.path !== current) await get().openSession(session.path)
    if ((useStatusStore.getState().status?.mode ?? 'zen') !== 'zen') {
      await get().setMode('zen') // 默认全自动（用户在太极里仍可改回 ask）
    }
    useWorkPanelStore.getState().showTab('taiji')
  },

  async exitTaiji() {
    const back = get().taijiReturn
    set({ taijiReturn: null })
    if (back === null) return
    if (back.path !== '') await get().openSession(back.path)
    const mode = useStatusStore.getState().status?.mode ?? 'zen'
    if (mode !== back.mode) await get().setMode(back.mode)
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
    if (showFrameError(frame)) return false
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
    if (showFrameError(frame)) return null
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
    if (showFrameError(frame)) return
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
  if (showFrameError(frame)) return
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
  if (showFrameError(frame)) return
}

function showActionError(value: unknown): void {
  useNoticeStore.getState().show({
    kind: 'error',
    text: value instanceof Error ? value.message : String(value),
  })
}

/** A server refusal goes through the code→dictionary mapper (`message` stays
 * the fallback for codes this build does not know); returns false when the
 * frame is not a refusal. */
function showFrameError(frame: ReplyLike): boolean {
  const text = frameErrorNotice(frame, useSettingsStore.getState().locale)
  if (text === null) return false
  useNoticeStore.getState().show({ kind: 'error', text })
  return true
}

async function refreshAfterSessionChange(get: () => AppState): Promise<void> {
  await get().refreshTranscript()
  await get().refreshStatus()
  await get().refreshSessions()
}

/** Re-apply the user's mode/model/thinking onto a freshly spawned child.
 *
 * The child comes up with `--mode ask` and catalog defaults; without this a
 * project switch silently drops the person out of zen mode and back to the
 * default model, which is exactly the "everything reset" feeling a reload
 * has. Every replayed frame is a normal request: a pref the server rejects
 * (a model that was renamed, say) costs that pref, never the connection. */
async function restorePrefs(get: () => AppState): Promise<void> {
  await get().refreshStatus()
  const prefs = loadPrefs()
  const live = useStatusStore.getState().status
  const frames = replayFrames(prefs, {
    mode: live?.mode ?? null,
    model: live?.model ?? null,
    thinking: live?.thinking ?? null,
  })
  if (frames.length === 0) return
  for (const payload of frames) {
    try {
      await get().client().send(payload)
    } catch {
      // A dead child ends here; the caller's own error path reports it.
      return
    }
  }
  await get().refreshStatus()
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
