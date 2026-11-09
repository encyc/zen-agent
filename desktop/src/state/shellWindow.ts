/** When the shell window may become visible.
 *
 * The window boots hidden (`tauri.conf.json` `visible: false`) so the person
 * never sees an empty webview. It may appear once the ready tree is committed
 * — and also when boot failed, because the ExitScreen/RuntimeScreen is exactly
 * what they need to see. Idle and starting still render the boot surface and
 * stay hidden. */

import type { ConnectionState } from './appStore.ts'

/** When the unconditional boot reveal fires: after the ready-effect hints
 * (rAF and 500ms), so it only ever rescues a window nothing else showed. */
export const BOOT_REVEAL_FALLBACK_MS = 1800

export function shouldShowWindow(connection: ConnectionState): boolean {
  return connection === 'ready' || connection === 'exited'
}

/** One owner for "show the shell window".
 *
 * The ready effect and the boot fallback timer both call the same revealer:
 * the first call starts `show`, later calls await it instead of issuing a
 * second `plugin:window|show`. The pending flag clears only on rejection, so
 * a failed show lets the other caller retry rather than leaving the window
 * hidden forever. */
export function createWindowRevealer(show: () => Promise<unknown>): () => Promise<void> {
  let pending: Promise<void> | null = null
  return () => {
    if (pending !== null) return pending
    pending = Promise.resolve()
      .then(show)
      .then(() => undefined)
      .catch((error: unknown) => {
        pending = null
        throw error
      })
    return pending
  }
}
