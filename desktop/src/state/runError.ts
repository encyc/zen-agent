/** Surface a provider run failure exactly once — inline, not as a popup.
 *
 * `status.last_error` stays set until the next run starts, while the status
 * poll repeats every 2s. Re-showing the same message would stack duplicate
 * rows; an empty reading (the next run) re-arms it, matching the TUI clearing
 * `last_error`.
 *
 * The line belongs in the transcript: a failed run leaves the retry row (when
 * the provider was retried) directly above it, so the count, the elapsed
 * backoff and the reason read as one story. The notice banner is kept for
 * failures with no turn to sit in — boot, export results, RPC refusals. */

import { useChatStore } from './chatStore.ts'
import { useNoticeStore } from './stores.ts'

let surfaced = ''

export function surfaceRunError(lastError: string | undefined): void {
  const text = lastError?.trim() ?? ''
  if (text === '') {
    if (surfaced !== '' && useNoticeStore.getState().notice?.text === surfaced) {
      useNoticeStore.getState().dismiss()
    }
    surfaced = ''
    return
  }
  if (text === surfaced) return
  surfaced = text
  if (useChatStore.getState().nodes.length > 0) {
    useChatStore.getState().pushFailure(text)
    return
  }
  useNoticeStore.getState().show({ kind: 'error', text })
}
