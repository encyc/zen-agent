/** Settings → Zen and Settings → Swarm: the two multi-agent surfaces.
 *
 * Zen holds the fleet's runtime knobs plus the leave-behavior toggle. Swarm
 * owns the project's room policy, the role roster (the same CRUD the room
 * sidebar offers) and the library copy entry. Both pages edit the same
 * `[zen]`/`[swarm]` config tables, so their knobs reuse the Parameters row. */

import { useState } from 'react'
import { useT } from '../../i18n/locale'
import type { SwarmRole } from '../../rpc/types'
import { useAppStore } from '../../state/appStore'
import { projectName } from '../../state/sessionGroups'
import { useSettingsStore } from '../../state/stores'
import { useSwarmStore } from '../../state/swarmStore'
import { AgentSettings } from './AgentSettings'
import { KnobPanel } from './KnobPanel'
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
      <KnobPanel
        table="zen"
        extraKeys={['task.timeout_s']}
        title={t('zen.settings.fleetParams')}
        caption={t('zen.settings.fleetParamsHint')}
      />
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

function missingCredentials(roles: SwarmRole[]): string[] {
  return [...new Set(roles.flatMap((role) => role.missingCredentials))].sort()
}

export function SwarmSettings(props: { onNavigate(tab: Tab): void }) {
  const t = useT()
  const project = useAppStore((state) => state.project)
  const roles = useSwarmStore((state) => state.roles)
  const loaded = useSwarmStore((state) => state.loaded)
  const loadedProject = useSwarmStore((state) => state.loadedProject)
  const [picking, setPicking] = useState(false)
  const missing = missingCredentials(roles)
  // "All present" is only meaningful once this project's roster is in.
  const healthKnown = loaded && loadedProject === project
  return (
    <div className={css.stack} data-settings-swarm>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div>
            <h3 className={css.cardHeading}>{t('swarm.settings.projectTitle')}</h3>
            <p className={css.cardCaption}>{t('swarm.settings.desc')}</p>
          </div>
        </div>
        <p className={css.projectPath}>
          {project === null ? t('swarm.settings.noProject') : projectName(project)}
          {project === null ? null : <code className={css.projectRoot}>{project}</code>}
        </p>
        {healthKnown ? (
          <div className={css.healthRow}>
            <span data-health={missing.length === 0 ? 'ok' : 'missing'}>
              {missing.length === 0
                ? t('swarm.settings.credentialsOk')
                : t('swarm.settings.credentialsMissing', { keys: missing.join(', ') })}
            </span>
            {missing.length > 0 ? (
              <button
                type="button"
                className={css.action}
                onClick={() => props.onNavigate('models')}
              >
                {t('swarm.settings.fixCredentials')}
              </button>
            ) : null}
          </div>
        ) : null}
      </section>

      <KnobPanel
        table="swarm"
        title={t('swarm.settings.policy')}
        caption={t('swarm.settings.policyHint')}
      />

      <AgentSettings />

      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div>
            <h3 className={css.cardHeading}>{t('swarm.settings.library')}</h3>
            <p className={css.cardCaption}>{t('swarm.settings.libraryHint')}</p>
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
        </div>
      </section>
      {picking && <LibraryPicker onClose={() => setPicking(false)} onCopied={() => {}} />}
    </div>
  )
}
