/** The user's mode/model/thinking replayed onto a freshly spawned child.
 *
 * Moved verbatim out of `appStore.ts` (that file may only shrink) so the
 * detached session window's boot can replay the same choices without owning
 * the whole orchestrator. Every replayed frame is a normal request: a pref
 * the server rejects (a renamed model, say) costs that pref, never the
 * connection. */

import { useStatusStore } from './stores.ts'
import { loadPrefs, replayFrames } from './prefs.ts'

interface AppGetterLike {
  client(): { send(payload: Record<string, unknown>): Promise<Record<string, unknown>> }
  refreshStatus(): Promise<void>
}

export function restorePrefs(getApp: () => AppGetterLike): Promise<void> {
  return restore(getApp())
}

async function restore(get: AppGetterLike): Promise<void> {
  await get.refreshStatus()
  const prefs = loadPrefs()
  const live = useStatusStore.getState().status
  const frames = replayFrames(prefs, {
    mode: live?.mode ?? null,
    model: live?.model ?? null,
    thinking: live?.thinking ?? null,
  })
  if (frames.length === 0) return
  for (const payload of frames) {
    try {
      await get.client().send(payload)
    } catch {
      // A dead child ends here; the caller's own error path reports it.
      return
    }
  }
  await get.refreshStatus()
}
