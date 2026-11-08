/** Locale glue for the desktop chrome: `useT` reads the settings store's
 * locale and hands components a translate function. The dictionaries and the
 * pure translate() live in ./messages (node --test loads them directly). */

import { translate } from './messages.ts'
import { useSettingsStore } from '../state/stores'

export { translate }
export type { Locale } from './messages.ts'

export function useT(): (key: string, params?: Record<string, string | number>) => string {
  const locale = useSettingsStore((state) => state.locale)
  return (key, params) => translate(locale, key, params)
}
