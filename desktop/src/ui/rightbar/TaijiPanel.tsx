/** Taiji tab: the repository's task graph — commit lanes with the zen
 * sessions that live on each branch. Read-only in P0; the Router session
 * stays the only way to act on a task. */

import { useCallback, useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useT } from '../../i18n/locale'
import type { TaijiGraph, TaijiProject, TaijiResult, TaijiSessionMap } from '../../rpc/types'
import { BranchIcon, RefreshIcon } from '../icons'
import css from './TaijiPanel.module.css'

const REQUEST_TIMEOUT_MS = 8000

export function TaijiPanel() {
  const t = useT()
  const [graph, setGraph] = useState<TaijiGraph | null>(null)
  const [sessions, setSessions] = useState<TaijiSessionMap | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      const frame = await useAppStore
        .getState()
        .client()
        .send({ type: 'taiji', op: 'snapshot' }, REQUEST_TIMEOUT_MS)
      const result = frame as unknown as TaijiResult
      if (result.ok !== true) throw new Error(String(frame.message ?? 'taiji failed'))
      setGraph(result.taiji.graph)
      setSessions(result.taiji.sessions)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (failed) return <p className={css.note}>{t('panel.taiji.error')}</p>
  if (graph === null || sessions === null) return <p className={css.note}>{t('panel.taiji.loading')}</p>
  if (!graph.is_repo) return <p className={css.note}>{t('panel.taiji.empty')}</p>

  return (
    <div className={css.root}>
      <div className={css.header}>
        <BranchIcon size={13} />
        <span className={css.title}>{t('panel.taiji')}</span>
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.taiji.refresh')}
          title={t('panel.taiji.refresh')}
          onClick={() => void load()}
        >
          <RefreshIcon size={13} />
        </button>
      </div>
      <ul className={css.list}>
        {graph.commits.map((commit) => {
          const branch = graph.branches.find((entry) => entry.sha === commit.sha)
          const project = sessions.projects.find((entry) => entry.branch === branch?.name)
          return (
            <li key={commit.sha} className={css.item}>
              <div className={css.itemHead}>
                <span className={css.short}>{commit.short}</span>
                <span className={css.subject}>{commit.subject}</span>
                {branch && (
                  <span className={css.badge} data-head={branch.is_head || undefined}>
                    {branch.name}
                  </span>
                )}
              </div>
              {project && project.sessions.length > 0 && (
                <div className={css.sessions}>
                  {project.sessions.map((session) => (
                    <span key={session.name} className={css.session} title={session.title}>
                      {session.title}
                    </span>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export type { TaijiProject }
