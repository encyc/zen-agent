/** Background refreshes must not die silently: a failed poll leaves a stale
 * list with no hint anything went wrong. Surface one line — the notice banner
 * shows it once and the notification log keeps it out of the way. */

import { translate } from '../i18n/messages.ts'
import { useNoticeStore, useSettingsStore } from './stores.ts'

export function reportRefreshFailure(scope: string, error: unknown): void {
  const locale = useSettingsStore.getState().locale
  const detail = error instanceof Error ? error.message : String(error)
  useNoticeStore.getState().show({
    kind: 'error',
    text: translate(locale, 'app.refreshFailed', { scope, error: detail }),
  })
}
