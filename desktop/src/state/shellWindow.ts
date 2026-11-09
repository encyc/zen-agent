/** When the shell window may become visible.
 *
 * The window boots hidden (`tauri.conf.json` `visible: false`) so the person
 * never sees an empty webview. It may appear once the ready tree is committed
 * — and also when boot failed, because the ExitScreen/RuntimeScreen is exactly
 * what they need to see. Idle and starting still render the boot surface and
 * stay hidden. */

import type { ConnectionState } from './appStore.ts'

export function shouldShowWindow(connection: ConnectionState): boolean {
  return connection === 'ready' || connection === 'exited'
}
