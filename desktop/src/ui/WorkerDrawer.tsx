/** Read-only drawer over one worker session's transcript (zen mode).

The worker keeps running in the background; this view never sends anything —
input lives only in the router conversation. Data comes from `zen_fleet
op=transcript`, the same view projection the main transcript uses. */

import { useEffect, useState } from 'react'

import { useAppStore } from '../state/appStore'
import { useZenStore } from '../state/zenStore'
import { useT } from '../i18n/locale'
import { XIcon } from './icons'
import css from './WorkerDrawer.module.css'

interface DrawerRow {
  kind?: string
  text?: string
  name?: string
  status?: string
}

export function WorkerDrawer() {
  const t = useT()
  const workerPath = useZenStore((state) => state.workerPath)
  const closeWorker = useZenStore((state) => state.closeWorker)
  const zenTranscript = useAppStore((state) => state.zenTranscript)
  const [rows, setRows] = useState<DrawerRow[]>([])
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (workerPath === null) return
    let alive = true
    setRows([])
    setFailed(false)
    void (async () => {
      const frame = await zenTranscript(workerPath)
      if (!alive) return
      if (frame === null) {
        setFailed(true)
        return
      }
      const transcript = Array.isArray(frame.transcript) ? frame.transcript : []
      setRows(transcript as DrawerRow[])
    })()
    return () => {
      alive = false
    }
  }, [workerPath, zenTranscript])

  useEffect(() => {
    if (workerPath === null) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeWorker()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [workerPath, closeWorker])

  if (workerPath === null) return null

  return (
    <div className={css.backdrop} data-zen-drawer onClick={closeWorker}>
      <div
        className={css.panel}
        role="dialog"
        aria-label={t('zen.workerDrawer')}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={css.header}>
          <span className={css.title}>{t('zen.workerDrawer')}</span>
          <span className={css.path}>{workerPath.split('/').pop()}</span>
          <button
            type="button"
            className={css.close}
            aria-label={t('zen.drawerClose')}
            onClick={closeWorker}
          >
            <XIcon size={14} />
          </button>
        </div>
        <div className={css.body} data-zen-drawer-body>
          {failed ? (
            <div className={css.empty}>{t('zen.drawerFailed')}</div>
          ) : rows.length === 0 ? (
            <div className={css.empty}>…</div>
          ) : (
            rows.map((row, index) => (
              <div
                key={`${row.kind ?? 'tool'}-${row.name ?? row.text ?? index}`}
                className={css.row}
                data-kind={row.kind ?? 'tool'}
              >
                {row.kind === 'user' || row.kind === 'assistant' ? (
                  <span className={css.text}>{row.text}</span>
                ) : (
                  <span className={css.tool}>
                    {row.name}
                    {row.status ? ` · ${row.status}` : ''}
                  </span>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
