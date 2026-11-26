/** The user's mode/model/thinking replayed onto a freshly spawned child.
 *
 * Moved verbatim out of `appStore.ts` (that file may only shrink) so the
 * detached session window's boot can replay the same choices without owning
 * the whole orchestrator. Every replayed frame is a normal request: a pref
 * the server rejects (a renamed model, say) costs that pref, never the
 * connection. */

import { useCatalogStore, useNoticeStore, useStatusStore } from './stores.ts'
import { loadPrefs, replayFrames, savePref } from './prefs.ts'

interface AppGetterLike {
  client(): { send(payload: Record<string, unknown>): Promise<Record<string, unknown>> }
  refreshStatus(): Promise<void>
  refreshCatalog?(): Promise<void>
}

export function restorePrefs(getApp: () => AppGetterLike): Promise<void> {
  return restore(getApp())
}

async function restore(get: AppGetterLike): Promise<void> {
  await get.refreshStatus()
  await get.refreshCatalog?.()
  const prefs = loadPrefs()
  // A pref written by 0.15.2 is a bare id; the server refuses those now, so
  // pin the one catalog match before replaying. A duplicate stays bare and is
  // refused with the qualified choices (then dropped, see below).
  if (prefs.model !== null && !prefs.model.includes('/')) {
    const matches = useCatalogStore
      .getState()
      .models.filter((model) => model.id === prefs.model)
    if (matches.length === 1) {
      prefs.model = `${matches[0].provider}/${matches[0].id}`
      savePref('model', prefs.model)
    }
  }
  const live = useStatusStore.getState().status
  const frames = replayFrames(prefs, {
    mode: live?.mode ?? null,
    // The qualified ref, so the replay cannot land on a mirror provider.
    model: live?.model_ref ?? live?.model ?? null,
    thinking: live?.thinking ?? null,
  })
  if (frames.length === 0) return
  for (const payload of frames) {
    try {
      const frame = await get.client().send(payload)
      // A refused pref (an ambiguous legacy bare id, a model whose key was
      // removed) must say why instead of silently leaving the default.
      const reason = typeof frame.reason === 'string' ? frame.reason.trim() : ''
      if (frame.ok === false && reason !== '') {
        // A legacy bare pref cannot be replayed under the provider-qualified
        // rule; drop it so every further spawn does not repeat the notice.
        if (
          payload.type === 'model' &&
          typeof prefs.model === 'string' &&
          !prefs.model.includes('/')
        ) {
          savePref('model', null)
        }
        useNoticeStore.getState().show({ kind: 'error', text: reason })
        return
      }
    } catch {
      // A dead child ends here; the caller's own error path reports it.
      return
    }
  }
  await get.refreshStatus()
}
