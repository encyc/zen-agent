/** The composer's one Tauri file-drop subscription.
 *
 * `listen` is async, so StrictMode (and any fast remount) can tear a mount
 * down before its registration settles. The cleanup therefore has to run the
 * unlisten once the promise arrives instead of skipping it: marking disposed
 * first and then checking `!disposed` inside the `.then` — the shape this
 * replaced — never unlistened at all, leaving one stale listener per mount
 * and duplicating every dropped image. */

import { listen as tauriListen } from '@tauri-apps/api/event'

export type DragDropPayload = { paths?: unknown }
export type DragDropHandler = (event: { payload: DragDropPayload }) => void
export type Unlisten = () => void
/** The Tauri `listen` shape, injectable so node tests need no webview. */
export type ListenFn = (event: string, handler: DragDropHandler) => Promise<Unlisten>

export const DRAG_DROP_EVENT = 'tauri://drag-drop'

/** Subscribe to `tauri://drag-drop` and hand the dropped paths over. The
 * returned cleanup always releases the registration, whenever it runs. */
export function attachDragDrop(
  onPaths: (paths: string[]) => void,
  listenFn: ListenFn = tauriListen,
): Unlisten {
  let disposed = false
  let unlisten: Unlisten | null = null

  void listenFn(DRAG_DROP_EVENT, (event) => {
    // A drop can arrive while the teardown is still waiting for unlisten.
    if (disposed) return
    const paths = event.payload.paths
    if (!Array.isArray(paths)) return
    const strings = paths.filter((path): path is string => typeof path === 'string')
    if (strings.length > 0) onPaths(strings)
  })
    .then((off) => {
      if (disposed) off()
      else unlisten = off
    })
    .catch(() => {
      // No bridge means no drops; the composer keeps working without them.
    })

  return () => {
    disposed = true
    unlisten?.()
    unlisten = null
  }
}
