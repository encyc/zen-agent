/** The zen fleet roster the sidebar renders while zen mode is on: the
 * project's single router row plus its focused workers (status dot, focus,
 * status word). Clicking a worker opens the read-only drawer; the router
 * row carries the fleet's explicit stop. */

import { useMemo } from 'react'

import { useAppStore } from '../state/appStore'
import { useZenStore } from '../state/zenStore'
import { useSettingsStore } from '../state/stores'
import { splitRoster, zenStatusKey, zenStatusTone } from '../state/zenSidebar'
import { useT } from '../i18n/locale'
import { StopIcon } from './icons'
import css from './Sidebar.module.css'

export function ZenRoster() {
  const t = useT()
  const openWorker = useZenStore((state) => state.openWorker)
  const zenRoster = useZenStore((state) => state.roster)
  const stoppedByUser = useZenStore((state) => state.stoppedByUser)
  const stopFleet = useAppStore((state) => state.stopFleet)
  const switchMode = useAppStore((state) => state.switchWorkspaceMode)
  const workspaceMode = useSettingsStore((state) => state.workspaceMode)
  const zenSplit = useMemo(() => splitRoster(zenRoster), [zenRoster])

  return (
    <div className={css.zenRoster} data-zen-roster>
      {stoppedByUser && workspaceMode === 'zen' && (
        <div className={css.zenPaused} data-zen-paused>
          <span>{t('zen.paused')}</span>
          <button
            type="button"
            className={css.zenResume}
            data-zen-resume
            onClick={() => switchMode('zen')}
          >
            {t('zen.pausedResume')}
          </button>
        </div>
      )}
      {zenSplit.router !== null && (
        <div className={css.zenRouterRow} data-zen-router>
          <span className={css.zenRouterBadge}>{t('zen.router')}</span>
          <span className={css.zenWorkerTitle}>{zenSplit.router.title}</span>
          <button
            type="button"
            className={css.zenStop}
            aria-label={t('zen.stopFleet')}
            title={t('zen.stopFleetTitle')}
            data-zen-stop
            onClick={() => void stopFleet()}
          >
            <StopIcon size={12} />
          </button>
        </div>
      )}
      <div className={css.zenWorkersLabel}>{t('zen.workers')}</div>
      <div className={css.zenWorkerList}>
        {zenSplit.workers.length > 0 ? (
          zenSplit.workers.map((worker) => (
            <button
              key={worker.path}
              type="button"
              className={css.zenWorkerRow}
              data-worker-row={worker.path}
              onClick={() => openWorker(worker.path)}
            >
              <span
                className={css.zenDot}
                data-tone={zenStatusTone(worker.status)}
                aria-hidden
              />
              <span className={css.zenWorkerTitle}>{worker.title}</span>
              {worker.focus !== '' && (
                <span className={css.zenFocus}>{worker.focus}</span>
              )}
              <span className={css.zenStatus}>{t(zenStatusKey(worker.status))}</span>
            </button>
          ))
        ) : (
          <div className={css.sectionEmpty}>{t('sidebar.empty')}</div>
        )}
      </div>
    </div>
  )
}
