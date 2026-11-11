/** The sidebar header's Coding <-> Zen switch: one persisted app-level toggle
 * (`zen.ui.mode`) that enables or disables the project's fleet on the backend. */

import { useAppStore } from '../state/appStore'
import { useSettingsStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { SparkIcon } from './icons'
import { Tooltip } from './primitives'
import css from './Sidebar.module.css'

export function ZenToggle() {
  const t = useT()
  const zenMode = useSettingsStore((state) => state.zenMode)
  const setZenMode = useAppStore((state) => state.setZenMode)

  return (
    <Tooltip label={t('zen.toggleHint')}>
      <button
        type="button"
        className={css.zenToggle}
        data-zen-toggle
        data-zen-active={zenMode === 'zen' || undefined}
        aria-label={t('zen.toggleHint')}
        onClick={() => void setZenMode(zenMode === 'zen' ? 'coding' : 'zen')}
      >
        <SparkIcon size={15} />
      </button>
    </Tooltip>
  )
}
