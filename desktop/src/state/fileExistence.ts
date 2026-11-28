/** Existence verdicts for the workspace paths an assistant message mentions.
 *
 * The chat linkifies file references into the Files panel; a reference to a
 * path that is not there opens onto the panel's could-not-read note. This
 * store lets the markdown renderer keep those references plain text: chips
 * whose path has a settled `false` verdict render without the link
 * affordance. Absence of a verdict keeps the link — a failed transport or a
 * not-yet-answered probe is not evidence of absence, so the ref behaves as
 * it always has until the server says otherwise.
 *
 * Verdicts live for the app's lifetime: a message that renders while its
 * file is missing stays plain even after the file appears (user-confirmed).
 */

import { useEffect, useMemo } from 'react'
import { create } from 'zustand'
import type { FsExistsResult } from '../rpc/types.ts'
import { useAppStore } from './appStore.ts'

const REQUEST_TIMEOUT_MS = 8000

type ExistsSender = (message: { type: 'fs_exists'; path: string }) => Promise<unknown>

interface FileExistenceState {
  /** path → exists. A path appears only once its request settled. */
  verdicts: Record<string, boolean>
  ensure(paths: readonly string[], send: ExistsSender): void
  /** Resolves when every in-flight probe has settled (tests await this). */
  drain(): Promise<void>
  reset(): void
}

/** One probe per path, app-wide: concurrent `ensure`s share the flight. */
const pending = new Map<string, Promise<void>>()

export const useFileExistenceStore = create<FileExistenceState>((set, get) => ({
  verdicts: {},
  ensure: (paths, send) => {
    for (const path of paths) {
      if (path in get().verdicts || pending.has(path)) continue
      // The probe body runs in a microtask so `pending.set` below always
      // lands first: a sender that throws synchronously must not let the
      // `finally` release a flight that was never registered.
      pending.set(
        path,
        Promise.resolve().then(async () => {
          try {
            const result = (await send({ type: 'fs_exists', path })) as Partial<FsExistsResult> & {
              code?: unknown
            }
            let verdict: boolean | null = null
            if (result.ok === true) verdict = result.exists === true
            else if (result.code === 'fs_error') verdict = false
            // Anything else (transport died, unknown frame): no verdict, the
            // ref keeps its link.
            if (verdict !== null) {
              set((state) => ({ verdicts: { ...state.verdicts, [path]: verdict } }))
            }
          } catch {
            // No verdict on a failed request; a later render probes again —
            // `pending` was cleared, so the next `ensure` may retry.
          } finally {
            pending.delete(path)
          }
        }),
      )
    }
  },
  reset: () => {
    pending.clear()
    set({ verdicts: {} })
  },
  drain: () => Promise.all([...pending.values()]).then(() => undefined),
}))

const FILE_REF_ATTR_RE = /data-file-path="([^"]*)"/gu

/** The workspace paths the rendered markdown currently offers as links, in
 * first-appearance order, deduplicated. The attribute values are our own
 * escaped markup, so unescaping is the exact inverse of escapeAttr — every
 * entity first, `&amp;` last (it was encoded first). */
export function collectFileRefs(html: string): string[] {
  const paths = new Set<string>()
  for (const match of html.matchAll(FILE_REF_ATTR_RE)) {
    const value = match[1]
      .replaceAll('&quot;', '"')
      .replaceAll('&#39;', "'")
      .replaceAll('&gt;', '>')
      .replaceAll('&lt;', '<')
      .replaceAll('&amp;', '&')
    if (value !== '') paths.add(value)
  }
  return [...paths]
}

/** Verdicts for one rendered markdown body: probes the refs it offers and
 * hands back the store's record for the renderer to consult. Stable identity
 * between verdict changes keeps `useMarkdown`'s memo honest. */
export function useCheckedFileRefs(html: string): Readonly<Record<string, boolean>> {
  const refs = useMemo(() => collectFileRefs(html), [html])
  const verdicts = useFileExistenceStore((state) => state.verdicts)
  useEffect(() => {
    if (refs.length === 0) return
    useFileExistenceStore.getState().ensure(refs, (message) =>
      useAppStore.getState().client().send(message, REQUEST_TIMEOUT_MS),
    )
  }, [refs])
  return verdicts
}
