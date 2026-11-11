/** Settings → Zen and Settings → Swarm: the two multi-agent surfaces.
 *
 * Zen holds the fleet's leave-behavior toggle (background run vs stop on
 * leave); Swarm explains the global room and jumps to the agents roster.
 * Self-contained like AgentSettings — SettingsPage keeps its own
 * Row/Switch internals, so this file carries its own primitives. */

import { useState } from 'react'
import { useT } from '../../i18n/locale'
import { useSettingsStore } from '../../state/stores'
import { LibraryPicker } from './LibraryPicker'
import type { Tab } from './settingsNav'
import css from './WorkspaceSettings.module.css'

function Switch(props: { checked: boolean; label: string; onChange(checked: boolean): void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={props.checked}
      aria-label={props.label}
      className={css.switch}
      onClick={() => props.onChange(!props.checked)}
    >
      <span className={css.switchThumb} />
    </button>
  )
}

export function ZenSettings() {
  const t = useT()
  const backgroundRun = useSettingsStore((state) => state.zenBackgroundRun)
  const setBackgroundRun = useSettingsStore((state) => state.setZenBackgroundRun)
  return (
    <div className={css.stack} data-settings-zen>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div>
            <h3 className={css.cardHeading}>{t('zen.settings.backgroundRun')}</h3>
            <p className={css.cardCaption}>{t('zen.settings.backgroundRunHint')}</p>
          </div>
          <Switch
            checked={backgroundRun}
            label={t('zen.settings.backgroundRun')}
            onChange={setBackgroundRun}
          />
        </div>
        <p className={css.cardCaption}>{t('zen.settings.stopNote')}</p>
      </section>
    </div>
  )
}

export function SwarmSettings(props: { onNavigate(tab: Tab): void }) {
  const t = useT()
  const [picking, setPicking] = useState(false)
  return (
    <div className={css.stack} data-settings-swarm>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div>
            <h3 className={css.cardHeading}>{t('settings.swarm')}</h3>
            <p className={css.cardCaption}>{t('swarm.settings.desc')}</p>
          </div>
        </div>
        <div className={css.cardActions}>
          <button
            type="button"
            className={css.action}
            data-settings-copy-role
            onClick={() => setPicking(true)}
          >
            {t('swarm.copyFromLibrary')}
          </button>
          <button
            type="button"
            className={css.action}
            onClick={() => props.onNavigate('agents')}
          >
            {t('swarm.settings.manageAgents')}
          </button>
        </div>
      </section>
      {picking && <LibraryPicker onClose={() => setPicking(false)} onCopied={() => {}} />}
    </div>
  )
}
