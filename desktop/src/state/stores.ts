/** Small stores: status snapshot, session list, catalogs, approvals,
 * composer draft, settings (theme/locale). */

import { create } from 'zustand'
import { CHAT_WIDTH_DEFAULT } from '../ui/columns.ts'
import { projectName } from './sessionGroups.ts'
import type {
  CommandEntry,
  ModelEntry,
  PendingMessage,
  SessionSummary,
  StatusResult,
} from '../rpc/types'

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
      return Array.isArray(parsed) ? parsed.slice(0, 8) : []
    }
    const legacy = localStorage.getItem(LEGACY_RECENTS_KEY)
    const parsed = legacy === null ? [] : (JSON.parse(legacy) as { path: string; at: number }[])
    const migrated = Array.isArray(parsed)
      ? parsed
          .slice(0, 8)
          .map((item) => ({ path: item.path, name: projectName(item.path), at: item.at }))
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
}

export const useProjectStore = create<ProjectState>((set) => ({
  projects: loadProjects(),
  addProject: (path) => {
    if (path === '') return
    set((state) => {
      const next = [
        { path, name: projectName(path), at: Date.now() },
        ...state.projects.filter((project) => project.path !== path),
      ].slice(0, 8)
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

export type WorkPanelTab = 'review' | 'context' | 'subagent'

export interface SubagentPanelTarget {
  callId: string
  name: string
}

interface WorkPanelState {
  open: boolean
  tab: WorkPanelTab
  subagent: SubagentPanelTarget | null
  openPanel(): void
  closePanel(): void
  showTab(tab: 'review' | 'context'): void
  openSubagent(target: SubagentPanelTarget): void
  closeSubagent(): void
}

export const useWorkPanelStore = create<WorkPanelState>((set) => ({
  open: false,
  tab: 'review',
  subagent: null,
  openPanel: () => set({ open: true }),
  closePanel: () => set({ open: false }),
  showTab: (tab) => set({ tab }),
  openSubagent: (subagent) => set({ open: true, tab: 'subagent', subagent }),
  closeSubagent: () =>
    set((state) => ({
      subagent: null,
      tab: state.tab === 'subagent' ? 'review' : state.tab,
    })),
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
  view: AppView
  setTheme(theme: ThemeMode): void
  setLocale(locale: Locale): void
  setSidebarCollapsed(collapsed: boolean): void
  setChatWidth(width: number): void
  setFontScale(scale: number): void
  setSessionTime(enabled: boolean): void
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
  setView: (view) => set({ view }),
}))

export function currentPending(status: StatusResult | null): PendingMessage[] {
  return status?.pending ?? []
}
