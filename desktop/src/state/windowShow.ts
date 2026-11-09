/** The real reveal: Tauri-only, shared by the ready effect and the boot
 * fail-safe, so the shell window is shown at most once.
 *
 * Kept apart from `shellWindow.ts` because this module imports the Tauri
 * webview API; the pure decision helpers stay loadable in node tests. */

import { isTauri } from '@tauri-apps/api/core'
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow'
import { createWindowRevealer } from './shellWindow.ts'

const revealOnce = createWindowRevealer(async () => {
  if (!isTauri()) return
  await getCurrentWebviewWindow().show()
})

/** Show the shell window exactly once; a rejected show is retryable by the
 * next caller. Never rejects: a reveal failure must not break boot. */
export function showShellWindow(): Promise<void> {
  return revealOnce().catch(() => {})
}
