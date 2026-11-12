/** Surface a provider run failure exactly once.
 *
 * `status.last_error` stays set until the next run starts, while the status
 * poll repeats every 2s. Re-showing the same message would re-raise the
 * banner forever; an empty reading (the next run) re-arms it and dismisses
 * the banner this module raised, matching the TUI clearing `last_error`. */

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
  useNoticeStore.getState().show({ kind: 'error', text })
}
