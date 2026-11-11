/** The zen fleet roster the sidebar renders while zen mode is on: the
 * project's single router row plus its focused workers (status dot, focus,
 * status word). Clicking a worker opens the read-only drawer. */

import { useMemo } from 'react'

import { useZenStore } from '../state/zenStore'
import { splitRoster, zenStatusKey, zenStatusTone } from '../state/zenSidebar'
import { useT } from '../i18n/locale'
import css from './Sidebar.module.css'

export function ZenRoster() {
  const t = useT()
  const openWorker = useZenStore((state) => state.openWorker)
  const zenRoster = useZenStore((state) => state.roster)
  const zenSplit = useMemo(() => splitRoster(zenRoster), [zenRoster])

  return (
    <div className={css.zenRoster} data-zen-roster>
      {zenSplit.router !== null && (
        <div className={css.zenRouterRow} data-zen-router>
          <span className={css.zenRouterBadge}>{t('zen.router')}</span>
          <span className={css.zenWorkerTitle}>{zenSplit.router.title}</span>
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
