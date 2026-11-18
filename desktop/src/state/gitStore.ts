/** Git tab state: the changes list and the per-file diff detail.
 *
 * `loadChanges` is polled (mounted, manual, and 500ms after the transcript
 * grows while the work panel is open — agent edits should appear without a
 * manual refresh); per-file diffs are fetched once per path+stage key and
 * cached for the store's lifetime. A failed refresh keeps the last payload
 * and flags `failed`, so the panel can offer retry over stale data instead
 * of flashing empty.
 */

import { create } from 'zustand'
import { useAppStore } from './appStore.ts'
import { useChatStore } from './chatStore.ts'
import { useWorkPanelStore } from './stores.ts'
import type { GitChangesPayload, GitChangesResult, GitDiffPayload, GitDiffResult } from '../rpc/types.ts'

const REQUEST_TIMEOUT_MS = 8000
const REFRESH_DEBOUNCE_MS = 500

export interface GitSelection {
  kind: 'file'
  path: string
  staged: boolean
}

interface GitState {
  changes: GitChangesPayload | null
  selected: GitSelection | null
  failed: boolean
  loadChanges(): Promise<void>
  openFile(path: string, staged: boolean): Promise<void>
  diffFor(key: string): GitDiffPayload | null
  clear(): void
}

/** Keyed `path:('s'|'w')` — staged and unstaged views of one path differ. */
const diffCache = new Map<string, GitDiffPayload>()

export const useGitStore = create<GitState>((set, get) => ({
  changes: null,
  selected: null,
  failed: false,

  loadChanges: async () => {
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitChangesResult>({ type: 'git_changes' }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) throw new Error('git_changes failed')
      set({ changes: result.changes, failed: false })
    } catch {
      set({ failed: true })
    }
  },

  openFile: async (path, staged) => {
    const selection: GitSelection = { kind: 'file', path, staged }
    set({ selected: selection })
    const key = `${path}:${staged ? 's' : 'w'}`
    if (diffCache.has(key)) return
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitDiffResult>({ type: 'git_diff', path, staged }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) throw new Error('git_diff failed')
      diffCache.set(key, result.diff)
      if (get().selected === selection) set({ selected: { ...selection } })
    } catch {
      // Uncached: the detail view keeps its loading/retire affordance.
    }
  },

  diffFor: (key) => diffCache.get(key) ?? null,

  clear: () => {
    diffCache.clear()
    set({ changes: null, selected: null, failed: false })
  },
}))

let refreshTimer: ReturnType<typeof setTimeout> | null = null

/** Agent tool calls append transcript nodes; while the work panel is open a
 * burst of growth collapses into one delayed refresh. */
useChatStore.subscribe((state, prev) => {
  if (state.nodes.length === prev.nodes.length) return
  if (!useWorkPanelStore.getState().open) return
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    void useGitStore.getState().loadChanges()
  }, REFRESH_DEBOUNCE_MS)
})
