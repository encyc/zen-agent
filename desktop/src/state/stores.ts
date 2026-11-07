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

export type WorkPanelTabKind = 'review' | 'files' | 'worktree' | 'taiji' | 'subagent'

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

const REVIEW_TAB: WorkPanelTab = { id: 'review', kind: 'review' }

interface WorkPanelState {
  open: boolean
  tabs: WorkPanelTab[]
  activeId: string
  openPanel(): void
  closePanel(): void
  showTab(kind: 'review' | 'files' | 'worktree' | 'taiji'): void
  activate(id: string): void
  openSubagent(target: SubagentPanelTarget): void
  closeTab(id: string): void
}

function withTab(tabs: WorkPanelTab[], tab: WorkPanelTab): WorkPanelTab[] {
  return tabs.some((entry) => entry.id === tab.id) ? tabs : [...tabs, tab]
}

export const useWorkPanelStore = create<WorkPanelState>((set) => ({
  open: false,
  tabs: [REVIEW_TAB],
  activeId: REVIEW_TAB.id,
  openPanel: () => set({ open: true }),
  closePanel: () => set({ open: false }),
  showTab: (kind) =>
    set((state) => ({
      open: true,
      tabs: withTab(state.tabs, { id: kind, kind }),
      activeId: kind,
    })),
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
      if (id === REVIEW_TAB.id) return state
      const tabs = state.tabs.filter((tab) => tab.id !== id)
      return {
        tabs,
        activeId: state.activeId === id ? REVIEW_TAB.id : state.activeId,
      }
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

// ---- approvals ---------------------------------------------------------------

export interface PendingApproval {
  requestId: string
  toolName: string
  args: string
  risk: string | null
  target: string | null
  reason: string | null
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
export type Locale = 'en' | 'zh'
export type AppView = 'chat' | 'settings' | 'tools'

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
  setTheme(theme: ThemeMode): void
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

export const useSettingsStore = create<SettingsState>((set) => ({
  theme: load('zen.theme', 'auto' as ThemeMode),
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
  setTheme: (theme) => {
    persist('zen.theme', theme)
    set({ theme })
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
}))

export function currentPending(status: StatusResult | null): PendingMessage[] {
  return status?.pending ?? []
}
