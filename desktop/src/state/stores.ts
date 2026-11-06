/** Small stores: status snapshot, session list, catalogs, approvals,
 * composer draft, settings (theme/locale). */

import { create } from 'zustand'
import type {
  CommandEntry,
  ModelEntry,
  PendingMessage,
  SessionSummary,
  StatusResult,
} from '../rpc/types'
import type { SessionScope } from './sessionGroups'

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
  scope: SessionScope
  setSessions(sessions: SessionSummary[]): void
  setCurrent(path: string | null): void
  setScope(scope: SessionScope): void
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  currentPath: null,
  scope: load('zen.sidebar.scope', 'project' as SessionScope),
  setSessions: (sessions) => set({ sessions }),
  setCurrent: (currentPath) => set({ currentPath }),
  setScope: (scope) => {
    persist('zen.sidebar.scope', scope)
    set({ scope })
  },
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
  setTheme(theme: ThemeMode): void
  setLocale(locale: Locale): void
  setSidebarCollapsed(collapsed: boolean): void
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
}))

export function currentPending(status: StatusResult | null): PendingMessage[] {
  return status?.pending ?? []
}
