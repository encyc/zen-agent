/** Lists the tools the connected agent exposes over the existing `tools`
 * request. Read-only: the page never mutates the catalog. */

import { useCallback, useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useSettingsStore } from '../../state/stores'
import { frameError } from '../../rpc/frames'
import type { ToolRow, ToolsResult } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { ArrowLeftIcon, RefreshIcon } from '../icons'
import css from './ToolsPage.module.css'

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; tools: ToolRow[] }

export function ToolsPage() {
  const t = useT()
  const setView = useSettingsStore((state) => state.setView)

  return (
    <div className={css.page} data-page="tools">
      <header className={css.header}>
        <button
          type="button"
          className={css.back}
          aria-label={t('common.back')}
          onClick={() => setView('chat')}
          autoFocus
        >
          <ArrowLeftIcon size={15} />
          <span>{t('common.back')}</span>
        </button>
        <h1 className={css.title}>{t('tools.title')}</h1>
      </header>
      <ToolList />
    </div>
  )
}

function ToolList() {
  const t = useT()
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  const load = useCallback(() => {
    setState({ status: 'loading' })
    void useAppStore
      .getState()
      .client()
      .send<ToolsResult>({ type: 'tools' })
      .then((frame) => {
        const error = frameError(frame)
        if (error !== null) {
          setState({ status: 'error', message: error })
          return
        }
        const tools = Array.isArray(frame.tools) ? frame.tools : []
        setState({ status: 'ready', tools })
      })
      .catch((reason: unknown) => {
        setState({ status: 'error', message: String(reason) })
      })
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (state.status === 'loading') {
    return <div className={css.status}>{t('tools.loading')}</div>
  }

  if (state.status === 'error') {
    return (
      <div className={css.status}>
        <p>{state.message}</p>
        <button type="button" className={css.retry} onClick={load}>
          <RefreshIcon size={13} />
          <span>{t('common.retry')}</span>
        </button>
      </div>
    )
  }

  if (state.tools.length === 0) {
    return <div className={css.status}>{t('tools.empty')}</div>
  }

  return (
    <div className={css.body}>
      <ul className={css.list}>
        {state.tools.map((tool) => (
          <li key={tool.name} className={css.row}>
            <div className={css.rowHead}>
              <span className={css.name}>{tool.name}</span>
              <span className={css.badge} data-approval={tool.approval}>
                {tool.approval}
              </span>
            </div>
            <p className={css.description}>{tool.description}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}
