/** The coding/zen/swarm segmented switch under the brand header.
 *
 * It only reads the workspace mode and routes clicks through the app store's
 * switchWorkspaceMode, which persists the mode and runs the zen-side
 * transitions (enable on enter, gated stop on leave). It never touches the
 * surfaces themselves. */

import { useT } from '../i18n/locale'
import { useAppStore } from '../state/appStore'
import {
  WORKSPACE_MODES,
  useSettingsStore,
  type WorkspaceMode,
} from '../state/stores'
import css from './ModeSwitcher.module.css'

function labelKey(mode: WorkspaceMode): string {
  return `workspace.${mode}`
}

export function ModeSwitcher() {
  const mode = useSettingsStore((state) => state.workspaceMode)
  const switchMode = useAppStore((state) => state.switchWorkspaceMode)
  const t = useT()
  return (
    <div
      className={css.root}
      role="tablist"
      aria-label={t('workspace.switcher')}
      data-workspace-switcher
    >
      {WORKSPACE_MODES.map((entry) => (
        <button
          key={entry}
          type="button"
          role="tab"
          aria-selected={entry === mode}
          className={css.tab}
          data-active={entry === mode || undefined}
          data-workspace-mode={entry}
          onClick={() => switchMode(entry)}
        >
          {t(labelKey(entry))}
        </button>
      ))}
    </div>
  )
}
