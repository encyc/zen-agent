/** The coding/zen/swarm segmented switch under the brand header.
 *
 * Self-contained on purpose: the zen mode lands in its own worktree, and this
 * component is the merge boundary — it only reads and writes the workspace
 * mode, never the surfaces themselves. */

import { useT } from '../i18n/locale'
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
  const setMode = useSettingsStore((state) => state.setWorkspaceMode)
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
          onClick={() => setMode(entry)}
        >
          {t(labelKey(entry))}
        </button>
      ))}
    </div>
  )
}
