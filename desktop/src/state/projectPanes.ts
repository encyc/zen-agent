/** Retained transcript panes, keyed by resolved project root. Switching back
 * to a project paints its cached pane before the server snapshot arrives. */

import type { ChatPane } from './chatStore.ts'

export interface CachedPane {
  /** The session the cached pane belongs to: a different target must not
   * reuse it (the snapshot would replace it a moment later anyway). */
  session: string | null
  pane: ChatPane
}

/** Three projects cover the realistic back-and-forth; the host caps resident
 * slots at four, so a fourth cached pane would usually be stale anyway. */
export const PANE_CACHE_LIMIT = 3

export class PaneCache {
  private readonly limit: number
  private readonly entries = new Map<string, CachedPane>()

  constructor(limit = PANE_CACHE_LIMIT) {
    this.limit = Math.max(1, limit)
  }

  get(root: string): CachedPane | undefined {
    const entry = this.entries.get(root)
    if (entry === undefined) return undefined
    this.entries.delete(root)
    this.entries.set(root, entry)
    return entry
  }

  set(root: string, entry: CachedPane): void {
    this.entries.delete(root)
    this.entries.set(root, entry)
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value
      if (oldest === undefined) break
      this.entries.delete(oldest)
    }
  }

  delete(root: string): void {
    this.entries.delete(root)
  }

  clear(): void {
    this.entries.clear()
  }
}
