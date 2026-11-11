/** The coding/zen/swarm segmented switch under the brand header.
 *
 * It only reads the workspace mode and routes clicks through the app store's
 * switchWorkspaceMode, which persists the mode and runs the zen-side
 * transitions (enable on enter, gated stop on leave). It never touches the
 * surfaces themselves. The cross-mode activity cues (zen fleet dot, swarm
 * live-task count) read the stores without loading anything. */

import type { ReactNode } from 'react'
import { useT } from '../i18n/locale'
import { useAppStore } from '../state/appStore'
import {
  WORKSPACE_MODES,
  useSettingsStore,
  type WorkspaceMode,
} from '../state/stores'
import { useZenStore } from '../state/zenStore'
import { swarmActiveCount, useSwarmStore } from '../state/swarmStore'
import css from './ModeSwitcher.module.css'

function labelKey(mode: WorkspaceMode): string {
  return `workspace.${mode}`
}

export function ModeSwitcher() {
  const mode = useSettingsStore((state) => state.workspaceMode)
  const switchMode = useAppStore((state) => state.switchWorkspaceMode)
  const zenEnabled = useZenStore((state) => state.enabled)
  const swarmCount = useSwarmStore((state) => swarmActiveCount(state.loaded, state.tasks))
  const t = useT()
  return (
    <div
      className={css.root}
      role="tablist"
      aria-label={t('workspace.switcher')}
      data-workspace-switcher
    >
      <span className={css.thumb} data-mode={mode} aria-hidden />
      {WORKSPACE_MODES.map((entry) => {
        let indicator: ReactNode = null
        let ariaLabel: string | undefined
        if (entry === 'zen' && zenEnabled) {
          indicator = <span className={css.zenDot} data-zen-active aria-hidden />
          ariaLabel = `${t(labelKey(entry))} · ${t('workspace.zenActive')}`
        } else if (entry === 'swarm' && swarmCount > 0) {
          indicator = (
            <span className={css.swarmBadge} data-swarm-badge>
              {swarmCount}
            </span>
          )
          ariaLabel = `${t(labelKey(entry))} · ${t('workspace.swarmActive', { count: swarmCount })}`
        }
        return (
          <button
            key={entry}
            type="button"
            role="tab"
            aria-selected={entry === mode}
            aria-label={ariaLabel}
            className={css.tab}
            data-active={entry === mode || undefined}
            data-workspace-mode={entry}
            onClick={() => switchMode(entry)}
          >
            <span className={css.tabLabel}>{t(labelKey(entry))}</span>
            {indicator}
          </button>
        )
      })}
    </div>
  )
}