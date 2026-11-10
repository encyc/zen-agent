/** Small stores: status snapshot, session list, catalogs, approvals,
 * composer draft, settings (theme/locale). */

import { create } from 'zustand'
import { CHAT_WIDTH_DEFAULT } from '../ui/columns.ts'
import { projectName } from './sessionGroups.ts'
import type {
  AskQuestion,
  CommandEntry,
  McpServerRow,
  ModelEntry,
  PluginCatalogRow,
  PluginRow,
  PendingMessage,
  SecretProviderRow,
  SessionSummary,
  SkillRow,
  StatusResult,
} from '../rpc/types'
import type { AskDraft } from './askDraft.ts'

// ---- status ---------------------------------------------------------------

interface StatusState {
  status: StatusResult | null
  set(status: StatusResult): void
}

export const useStatusStore = create<StatusState>((set) => ({
  status: null,
  set: (status) => set({ status }),
}))

// ---- sessions ---------------------------------------------------------------

interface SessionState {
  sessions: SessionSummary[]
  currentPath: string | null
  collapsedProjects: string[]
  setSessions(sessions: SessionSummary[]): void
  setCurrent(path: string | null): void
  toggleProject(project: string): void
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  currentPath: null,
  collapsedProjects: load('zen.sidebar.projects', [] as string[]),
  setSessions: (sessions) => set({ sessions }),
  setCurrent: (currentPath) => set({ currentPath }),
  toggleProject: (project) =>
    set((state) => {
      const collapsedProjects = state.collapsedProjects.includes(project)
        ? state.collapsedProjects.filter((key) => key !== project)
        : [...state.collapsedProjects, project]
      persist('zen.sidebar.projects', collapsedProjects)
      return { collapsedProjects }
    }),
}))

// ---- projects ---------------------------------------------------------------

export interface ProjectEntry {
  path: string
  name: string
  at: number
}

const PROJECTS_KEY = 'zen.projects'
const LEGACY_RECENTS_KEY = 'zen.recent.projects'

/** Reads the registered rail; a pre-rail install migrates its recent folders
 * once, so the first launch after the upgrade keeps its history. */
function loadProjects(): ProjectEntry[] {
  try {
    const raw = localStorage.getItem(PROJECTS_KEY)
    if (raw !== null) {
      const parsed = JSON.parse(raw) as ProjectEntry[]
      return Array.isArray(parsed) ? parsed : []
    }
    const legacy = localStorage.getItem(LEGACY_RECENTS_KEY)
    const parsed = legacy === null ? [] : (JSON.parse(legacy) as { path: string; at: number }[])
    const migrated = Array.isArray(parsed)
      ? parsed.map((item) => ({ path: item.path, name: projectName(item.path), at: item.at }))
      : []
    persist(PROJECTS_KEY, migrated)
    return migrated
  } catch {
    return []
  }
}

interface ProjectState {
  projects: ProjectEntry[]
  addProject(path: string): void
  removeProject(path: string): void
  updateProject(path: string, next: string): void
}

export const useProjectStore = create<ProjectState>((set) => ({
  projects: loadProjects(),
  addProject: (path) => {
    if (path === '') return
    set((state) => {
      const next = [
        { path, name: projectName(path), at: Date.now() },
        ...state.projects.filter((project) => project.path !== path),
      ]
      persist(PROJECTS_KEY, next)
      return { projects: next }
    })
  },
  removeProject: (path) =>
    set((state) => {
      const next = state.projects.filter((project) => project.path !== path)
      persist(PROJECTS_KEY, next)
      return { projects: next }
    }),
  updateProject: (path, next) =>
    set((state) => {
      if (path === next) return {}
      const entries = state.projects
        .filter((project) => project.path !== next)
        .map((project) =>
          project.path === path
            ? { path: next, name: projectName(next), at: project.at }
            : project,
        )
      persist(PROJECTS_KEY, entries)
      return { projects: entries }
    }),
}))

// ---- notices ---------------------------------------------------------------

export interface Notice {
  kind: 'error' | 'info'
  text: string
}

interface NoticeState {
  notice: Notice | null
  show(notice: Notice): void
  dismiss(): void
}

export const useNoticeStore = create<NoticeState>((set) => ({
  notice: null,
  show: (notice) => {
    set({ notice })
    useNotificationStore.getState().push({ tone: notice.kind, text: notice.text })
  },
  dismiss: () => set({ notice: null }),
}))

// ---- notifications ---------------------------------------------------------

export interface NotificationItem {
  id: number
  tone: 'error' | 'info'
  text: string
  at: number
}

interface NotificationState {
  items: NotificationItem[]
  unread: number
  push(item: { tone: 'error' | 'info'; text: string }): void
  markRead(): void
  clear(): void
}

let notificationSeq = 0

export const useNotificationStore = create<NotificationState>((set) => ({
  items: [],
  unread: 0,
  push: ({ tone, text }) =>
    set((state) => {
      const item: NotificationItem = { id: (notificationSeq += 1), tone, text, at: Date.now() }
      return { items: [item, ...state.items].slice(0, 50), unread: state.unread + 1 }
    }),
  markRead: () => set({ unread: 0 }),
  clear: () => set({ items: [], unread: 0 }),
}))

// ---- command palette -------------------------------------------------------

interface PaletteState {
  open: boolean
  query: string
  openPalette(): void
  closePalette(): void
  setQuery(query: string): void
}

export const usePaletteStore = create<PaletteState>((set) => ({
  open: false,
  query: '',
  openPalette: () => set({ open: true, query: '' }),
  closePalette: () => set({ open: false, query: '' }),
  setQuery: (query) => set({ query }),
}))

// ---- work panel -------------------------------------------------------------

export type WorkPanelTabKind =
  | 'review'
  | 'files'
  | 'worktree'
  | 'memory'
  | 'terminal'
  | 'subagent'

export interface WorkPanelTab {
  id: string
  kind: WorkPanelTabKind
  callId?: string
  name?: string
}

export interface SubagentPanelTarget {
  callId: string
  name: string
}

/** One chat file reference asking the Files tab to open a path. The nonce lets
 * the same path be requested twice without the viewer swallowing the repeat. */
export interface FileOpenRequest {
  path: string
  nonce: number
}

const REVIEW_TAB: WorkPanelTab = { id: 'review', kind: 'review' }
const FILES_TAB: WorkPanelTab = { id: 'files', kind: 'files' }

let fileRequestSeq = 0

interface WorkPanelState {
  open: boolean
  tabs: WorkPanelTab[]
  activeId: string
  fileRequest: FileOpenRequest | null
  openPanel(): void
  closePanel(): void
  showTab(kind: 'review' | 'files' | 'worktree' | 'memory' | 'terminal'): void
  openFile(path: string): void
  consumeFileRequest(): void
  activate(id: string): void
  openSubagent(target: SubagentPanelTarget): void
  closeTab(id: string): void
}

function withTab(tabs: WorkPanelTab[], tab: WorkPanelTab): WorkPanelTab[] {
  return tabs.some((entry) => entry.id === tab.id) ? tabs : [...tabs, tab]
}

export const useWorkPanelStore = create<WorkPanelState>((set) => ({
  open: false,
  tabs: [REVIEW_TAB, FILES_TAB],
  activeId: FILES_TAB.id,
  fileRequest: null,
  // Every fresh open lands on files: the rail is a browsing surface first, so
  // a review (or subagent) tab left active must not swallow the next open.
  openPanel: () =>
    set((state) => ({
      open: true,
      tabs: withTab(state.tabs, FILES_TAB),
      activeId: FILES_TAB.id,
    })),
  closePanel: () => set({ open: false }),
  showTab: (kind) =>
    set((state) => ({
      open: true,
      tabs: withTab(state.tabs, { id: kind, kind }),
      activeId: kind,
    })),
  openFile: (path) =>
    set((state) => ({
      open: true,
      tabs: withTab(state.tabs, FILES_TAB),
      activeId: FILES_TAB.id,
      fileRequest: { path, nonce: (fileRequestSeq += 1) },
    })),
  consumeFileRequest: () => set({ fileRequest: null }),
  activate: (id) =>
    set((state) =>
      state.tabs.some((tab) => tab.id === id) ? { open: true, activeId: id } : state,
    ),
  openSubagent: (target) =>
    set((state) => {
      const id = `subagent:${target.callId}`
      return {
        open: true,
        tabs: withTab(state.tabs, {
          id,
          kind: 'subagent',
          callId: target.callId,
          name: target.name,
        }),
        activeId: id,
      }
    }),
  closeTab: (id) =>
    set((state) => {
      const tabs = state.tabs.filter((tab) => tab.id !== id)
      // Closing the last tab closes the rail: an empty dock has nothing to show.
      if (tabs.length === 0) return { tabs, activeId: '', open: false }
      if (tabs.some((tab) => tab.id === state.activeId)) return { tabs }
      const fallback = tabs.find((tab) => tab.id === FILES_TAB.id) ?? tabs[tabs.length - 1]
      return { tabs, activeId: fallback.id }
    }),
}))

// ---- catalogs ---------------------------------------------------------------

interface CatalogState {
  models: ModelEntry[]
  commands: CommandEntry[]
  setModels(models: ModelEntry[]): void
  setCommands(commands: CommandEntry[]): void
}

export const useCatalogStore = create<CatalogState>((set) => ({
  models: [],
  commands: [],
  setModels: (models) => set({ models }),
  setCommands: (commands) => set({ commands }),
}))

// ---- provider keys ----------------------------------------------------------

interface SecretsState {
  modelProviders: SecretProviderRow[]
  searchProviders: SecretProviderRow[]
  setProviders(modelProviders: SecretProviderRow[], searchProviders: SecretProviderRow[]): void
}

export const useSecretsStore = create<SecretsState>((set) => ({
  modelProviders: [],
  searchProviders: [],
  setProviders: (modelProviders, searchProviders) =>
    set({ modelProviders, searchProviders }),
}))

// ---- skills -----------------------------------------------------------------

interface SkillsState {
  skills: SkillRow[]
  setSkills(skills: SkillRow[]): void
}

export const useSkillsStore = create<SkillsState>((set) => ({
  skills: [],
  setSkills: (skills) => set({ skills }),
}))

// ---- mcp --------------------------------------------------------------------

interface McpState {
  servers: McpServerRow[]
  problem: string | null
  setServers(servers: McpServerRow[], problem: string | null): void
}

export const useMcpStore = create<McpState>((set) => ({
  servers: [],
  problem: null,
  setServers: (servers, problem) => set({ servers, problem }),
}))

// ---- plugins ----------------------------------------------------------------

export interface PluginState {
  plugins: PluginRow[]
  catalog: PluginCatalogRow[]
  problem: string | null
  applyPending: boolean
  setView(
    plugins: PluginRow[],
    catalog: PluginCatalogRow[],
    problem: string | null,
    applyPending?: boolean,
  ): void
}

export const usePluginStore = create<PluginState>((set) => ({
  plugins: [],
  catalog: [],
  problem: null,
  applyPending: false,
  setView: (plugins, catalog, problem, applyPending = false) =>
    set({ plugins, catalog, problem, applyPending }),
}))

// ---- approvals ---------------------------------------------------------------

export interface PendingApproval {
  requestId: string
  toolName: string
  args: string
  risk: string | null
  target: string | null
  reason: string | null
  /** The slot that asked; null for a host-level or v2-server frame. */
  project?: string | null
  /** The session that asked; a background sibling stays queued. */
  session?: string | null
}

interface ApprovalState {
  queue: PendingApproval[]
  push(approval: PendingApproval): void
  pop(requestId: string): void
  clear(): void
}

export const useApprovalStore = create<ApprovalState>((set) => ({
  queue: [],
  push: (approval) => set((state) => ({ queue: [...state.queue, approval] })),
  pop: (requestId) =>
    set((state) => ({ queue: state.queue.filter((item) => item.requestId !== requestId) })),
  clear: () => set({ queue: [] }),
}))

// ---- ask ----------------------------------------------------------------------

export interface PendingAsk {
  requestId: string
  questions: AskQuestion[]
  /** The slot that asked; null for a host-level or v2-server frame. */
  project?: string | null
  /** The session that asked; a background sibling stays queued. */
  session?: string | null
}

interface AskState {
  queue: PendingAsk[]
  drafts: Record<string, AskDraft>
  push(ask: PendingAsk): void
  pop(requestId: string): void
  setDraft(requestId: string, draft: AskDraft): void
  clear(): void
}

export const useAskStore = create<AskState>((set) => ({
  queue: [],
  drafts: {},
  push: (ask) =>
    set((state) =>
      state.queue.some((entry) => entry.requestId === ask.requestId)
        ? state
        : { queue: [...state.queue, ask] },
    ),
  pop: (requestId) =>
    set((state) => {
      const drafts = { ...state.drafts }
      delete drafts[requestId]
      return {
        queue: state.queue.filter((entry) => entry.requestId !== requestId),
        drafts,
      }
    }),
  setDraft: (requestId, draft) =>
    set((state) => ({ drafts: { ...state.drafts, [requestId]: draft } })),
  clear: () => ({ queue: [], drafts: {} }),
}))

// ---- composer -----------------------------------------------------------------

export interface AttachmentImage {
  id: string
  path: string
  name: string
}

interface ComposerState {
  draft: string
  images: AttachmentImage[]
  setDraft(draft: string): void
  addImages(paths: string[]): void
  removeImage(id: string): void
  clearImages(): void
}

let imageSeq = 0

export const useComposerStore = create<ComposerState>((set) => ({
  draft: '',
  images: [],
  setDraft: (draft) => set({ draft }),
  addImages: (paths) =>
    set((state) => ({
      images: [
        ...state.images,
        ...paths.map((path) => ({
          id: `img${(imageSeq += 1)}`,
          path,
          name: path.split('/').pop() ?? path,
        })),
      ],
    })),
  removeImage: (id) =>
    set((state) => ({ images: state.images.filter((image) => image.id !== id) })),
  clearImages: () => set({ images: [] }),
}))

// ---- settings (persisted) -------------------------------------------------------

export type ThemeMode = 'light' | 'dark' | 'auto'
/** Named palette layered on top of the mode: zen is the monochrome base. */
export type ThemeId = 'zen' | 'vscode' | 'github' | 'tokyonight'
export const THEME_IDS: readonly ThemeId[] = ['zen', 'vscode', 'github', 'tokyonight']
export type Locale = 'en' | 'zh'
export type AppView = 'chat' | 'settings'
/** Top-level surfaces: the coding chat, the swarm room, the zen placeholder.
 * The switcher stays mounted across all three. */
export type WorkspaceMode = 'coding' | 'zen' | 'swarm'

export const WORKSPACE_MODES: readonly WorkspaceMode[] = ['coding', 'zen', 'swarm']

export const FONT_SCALE_MIN = 0.9
export const FONT_SCALE_MAX = 1.2
export const FONT_SCALE_DEFAULT = 1

export type ContextUsageDisplay = 'remaining' | 'used'

export function clampFontScale(value: number): number {
  if (!Number.isFinite(value)) return FONT_SCALE_DEFAULT
  const clamped = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, value))
  return Math.round(clamped * 100) / 100
}

interface SettingsState {
  theme: ThemeMode
  themeId: ThemeId
  locale: Locale
  sidebarCollapsed: boolean
  chatWidth: number
  fontScale: number
  sessionTime: boolean
  updateChecks: boolean
  showThinking: boolean
  autoCollapseThinking: boolean
  contextUsageDisplay: ContextUsageDisplay
  enterToSend: boolean
  view: AppView
  workspaceMode: WorkspaceMode
  setTheme(theme: ThemeMode): void
  setThemeId(themeId: ThemeId): void
  setLocale(locale: Locale): void
  setSidebarCollapsed(collapsed: boolean): void
  setChatWidth(width: number): void
  setFontScale(scale: number): void
  setSessionTime(enabled: boolean): void
  setUpdateChecks(enabled: boolean): void
  setShowThinking(enabled: boolean): void
  setAutoCollapseThinking(enabled: boolean): void
  setContextUsageDisplay(display: ContextUsageDisplay): void
  setEnterToSend(enabled: boolean): void
  setView(view: AppView): void
  setWorkspaceMode(mode: WorkspaceMode): void
}

function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

function persist(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // storage unavailable — settings just do not persist
  }
}

/** Guards the persisted palette: pre-theme installs and foreign values fall
 * back to the zen base instead of rendering an unnamed palette. */
function loadThemeId(): ThemeId {
  const value = load<string>('zen.themeId', 'zen')
  return (THEME_IDS as readonly string[]).includes(value) ? (value as ThemeId) : 'zen'
}

/** A foreign persisted mode (a build without that surface) falls back to the
 * coding chat instead of rendering an unnameable surface. */
function loadWorkspaceMode(): WorkspaceMode {
  const value = load<string>('zen.workspaceMode', 'coding')
  return (WORKSPACE_MODES as readonly string[]).includes(value)
    ? (value as WorkspaceMode)
    : 'coding'
}

export const useSettingsStore = create<SettingsState>((set) => ({
  theme: load('zen.theme', 'auto' as ThemeMode),
  themeId: loadThemeId(),
  locale: load('zen.locale', 'zh' as Locale),
  sidebarCollapsed: load('zen.sidebar.collapsed', false),
  chatWidth: load('zen.chat.width', CHAT_WIDTH_DEFAULT),
  fontScale: clampFontScale(load('zen.fontScale', FONT_SCALE_DEFAULT)),
  sessionTime: load('zen.sidebar.sessionTime', true),
  updateChecks: load('zen.update.checks', true),
  showThinking: load('zen.chat.showThinking', true),
  autoCollapseThinking: load('zen.chat.autoCollapseThinking', true),
  contextUsageDisplay: load<ContextUsageDisplay>('zen.context.display', 'remaining') === 'used'
    ? 'used'
    : 'remaining',
  enterToSend: load('zen.composer.enterToSend', true),
  view: 'chat',
  workspaceMode: loadWorkspaceMode(),
  setTheme: (theme) => {
    persist('zen.theme', theme)
    set({ theme })
  },
  setThemeId: (themeId) => {
    persist('zen.themeId', themeId)
    set({ themeId })
  },
  setLocale: (locale) => {
    persist('zen.locale', locale)
    set({ locale })
  },
  setSidebarCollapsed: (sidebarCollapsed) => {
    persist('zen.sidebar.collapsed', sidebarCollapsed)
    set({ sidebarCollapsed })
  },
  setChatWidth: (chatWidth) => {
    persist('zen.chat.width', chatWidth)
    set({ chatWidth })
  },
  setFontScale: (fontScale) => {
    const next = clampFontScale(fontScale)
    persist('zen.fontScale', next)
    set({ fontScale: next })
  },
  setSessionTime: (sessionTime) => {
    persist('zen.sidebar.sessionTime', sessionTime)
    set({ sessionTime })
  },
  setUpdateChecks: (updateChecks) => {
    persist('zen.update.checks', updateChecks)
    set({ updateChecks })
  },
  setShowThinking: (showThinking) => {
    persist('zen.chat.showThinking', showThinking)
    set({ showThinking })
  },
  setAutoCollapseThinking: (autoCollapseThinking) => {
    persist('zen.chat.autoCollapseThinking', autoCollapseThinking)
    set({ autoCollapseThinking })
  },
  setContextUsageDisplay: (contextUsageDisplay) => {
    persist('zen.context.display', contextUsageDisplay)
    set({ contextUsageDisplay })
  },
  setEnterToSend: (enterToSend) => {
    persist('zen.composer.enterToSend', enterToSend)
    set({ enterToSend })
  },
  setView: (view) => set({ view }),
  setWorkspaceMode: (workspaceMode) => {
    persist('zen.workspaceMode', workspaceMode)
    set({ workspaceMode })
  },
}))

export function currentPending(status: StatusResult | null): PendingMessage[] {
  return status?.pending ?? []
}
