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
import type { GitChangesPayload, GitChangesResult, GitCommit, GitDiffPayload, GitDiffResult, GitLogResult, GitShowPayload, GitShowResult } from '../rpc/types.ts'
import { mergeCommits } from '../ui/rightbar/lanes.ts'

const REQUEST_TIMEOUT_MS = 8000
const REFRESH_DEBOUNCE_MS = 500
const LOG_PAGE_SIZE = 100

export interface GitSelection {
  kind: 'file'
  path: string
  staged: boolean
}

export interface GitCommitSelection {
  kind: 'commit'
  hash: string
}

/** A read-only preview of another linked worktree. `null` means the project
 * root the runtime is serving: the normal review surface. */
export interface GitScope {
  path: string
  branch: string
}

/** One diff cache key: the same path means different content per worktree. */
export function diffKey(scope: GitScope | null, path: string, staged: boolean): string {
  return `${scope?.path ?? ''}:${path}:${staged ? 's' : 'w'}`
}

interface GitState {
  changes: GitChangesPayload | null
  selected: GitSelection | GitCommitSelection | null
  scope: GitScope | null
  failed: boolean
  /** The graph page: accumulated commits (paged appends, keyed refreshes). */
  log: GitCommit[] | null
  hasMoreLog: boolean
  setScope(scope: GitScope | null): void
  loadChanges(): Promise<void>
  loadLog(): Promise<void>
  loadMoreLog(): Promise<void>
  openFile(path: string, staged: boolean): Promise<void>
  openCommit(hash: string): Promise<void>
  diffFor(key: string): GitDiffPayload | null
  showFor(hash: string): GitShowPayload | null
  clear(): void
}

/** Keyed by scope + `path:('s'|'w')` — staged/unstaged views differ, and so
 * does the same path in two worktrees. */
const diffCache = new Map<string, GitDiffPayload>()

/** Commit shows, keyed by the hash the graph asked for. */
const showCache = new Map<string, GitShowPayload>()

/** Where the next graph page starts: the accumulated length. */
let logSkip = 0

/** One tail page at a time: the scroll handler and the sentinel both fire at
 * the bottom, and two concurrent pages would advance `logSkip` twice (the
 * reader silently loses a page). */
let loadingMore = false

export const useGitStore = create<GitState>((set, get) => ({
  changes: null,
  selected: null,
  scope: null,
  failed: false,
  log: null,
  hasMoreLog: true,

  setScope: (scope) => {
    set({ scope, selected: null })
    // The scoped working tree's list is a different question; reload it now
    // rather than letting a stale list sit under the preview banner.
    void get().loadChanges()
  },

  loadChanges: async () => {
    const scope = get().scope
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitChangesResult>(
          { type: 'git_changes', ...(scope ? { worktree: scope.path } : {}) },
          REQUEST_TIMEOUT_MS,
        )
      if (result.ok !== true) throw new Error('git_changes failed')
      set({ changes: result.changes, failed: false })
    } catch {
      set({ failed: true })
    }
  },

  loadLog: async () => {
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitLogResult>({ type: 'git_log', limit: LOG_PAGE_SIZE, skip: 0 }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) throw new Error('git_log failed')
      logSkip = result.log.commits.length
      set((state) => ({
        // A keyed merge: new commits go in front, an already-loaded tail
        // (paged before the refresh) keeps its order.
        log: state.log === null ? result.log.commits : mergeCommits(state.log, result.log.commits),
        hasMoreLog: true,
        failed: false,
      }))
    } catch {
      set({ failed: true })
    }
  },

  loadMoreLog: async () => {
    if (loadingMore) return
    const { log, hasMoreLog } = get()
    if (log === null || !hasMoreLog) return
    loadingMore = true
    const skip = logSkip
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitLogResult>(
          { type: 'git_log', limit: LOG_PAGE_SIZE, skip },
          REQUEST_TIMEOUT_MS,
        )
      if (result.ok !== true) throw new Error('git_log failed')
      // A refresh that re-keyed the head while this page was in flight moved
      // the cursor; its merge wins and this stale page is dropped.
      if (logSkip !== skip) return
      const known = new Set(log.map((commit) => commit.hash))
      const fresh = result.log.commits.filter((commit) => !known.has(commit.hash))
      logSkip += result.log.commits.length
      set({
        log: [...log, ...fresh],
        // A short page (or an empty one) ends the history.
        hasMoreLog: result.log.commits.length >= LOG_PAGE_SIZE,
      })
    } catch {
      set({ failed: true })
    } finally {
      loadingMore = false
    }
  },

  openCommit: async (hash) => {
    set({ selected: { kind: 'commit', hash } })
    if (showCache.has(hash)) return
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitShowResult>({ type: 'git_show', commit: hash }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) throw new Error('git_show failed')
      showCache.set(hash, result.show)
      const current = get().selected
      if (current?.kind === 'commit' && current.hash === hash) {
        set({ selected: { kind: 'commit', hash } })
      }
    } catch {
      // Uncached: the detail view keeps its loading affordance.
    }
  },

  showFor: (hash) => showCache.get(hash) ?? null,

  openFile: async (path, staged) => {
    const selection: GitSelection = { kind: 'file', path, staged }
    set({ selected: selection })
    const scope = get().scope
    const key = diffKey(scope, path, staged)
    if (diffCache.has(key)) return
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitDiffResult>(
          { type: 'git_diff', path, staged, ...(scope ? { worktree: scope.path } : {}) },
          REQUEST_TIMEOUT_MS,
        )
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
    showCache.clear()
    logSkip = 0
    set({
      changes: null,
      selected: null,
      scope: null,
      failed: false,
      log: null,
      hasMoreLog: true,
    })
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
    const git = useGitStore.getState()
    void git.loadChanges()
    if (git.log !== null) void git.loadLog()
  }, REFRESH_DEBOUNCE_MS)
})
