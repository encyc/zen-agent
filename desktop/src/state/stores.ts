/** Small stores: status snapshot, session list, catalogs, approvals,
 * composer draft, settings (theme/locale). */

import { create } from 'zustand'
import { CHAT_WIDTH_DEFAULT } from '../ui/columns'
import { projectName } from './sessionGroups'
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
  show: (notice) => set({ notice }),
  dismiss: () => set({ notice: null }),
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

interface SettingsState {
  theme: ThemeMode
  locale: Locale
  sidebarCollapsed: boolean
  chatWidth: number
  setTheme(theme: ThemeMode): void
  setLocale(locale: Locale): void
  setSidebarCollapsed(collapsed: boolean): void
  setChatWidth(width: number): void
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
}))

export function currentPending(status: StatusResult | null): PendingMessage[] {
  return status?.pending ?? []
}
