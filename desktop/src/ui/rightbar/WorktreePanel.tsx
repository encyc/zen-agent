/** Worktree tab: the repository's worktree list with branch, path and the
 * current working tree. Switching restarts the runtime in another worktree,
 * because the RPC process is cwd-bound. */

import { useCallback, useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useStatusStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import type { GitWorktreesResult, WorktreeOverview } from '../../rpc/types'
import { BranchIcon, CheckIcon, RefreshIcon } from '../icons'
import css from './WorktreePanel.module.css'

const REQUEST_TIMEOUT_MS = 8000

export function WorktreePanel() {
  const t = useT()
  const running = useStatusStore((state) => state.status?.running === true)
  const [overview, setOverview] = useState<WorktreeOverview | null>(null)
  const [failed, setFailed] = useState(false)
  const [switching, setSwitching] = useState<string | null>(null)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      const frame = await useAppStore
        .getState()
        .client()
        .send({ type: 'git_worktrees' }, REQUEST_TIMEOUT_MS)
      const result = frame as unknown as GitWorktreesResult
      if (result.ok !== true) throw new Error(String(frame.message ?? 'git_worktrees failed'))
      setOverview(result.worktree)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const switchTo = async (path: string) => {
    if (switching !== null || running) return
    setSwitching(path)
    try {
      // Another worktree is another project key: same runtime swap, same
      // mounted shell. openProject owns the pid/exit races; the panel only guards its button.
      await useAppStore.getState().openProject(path)
    } finally {
      setSwitching(null)
    }
  }

  if (failed) return <p className={css.note}>{t('panel.worktree.error')}</p>
  if (overview === null) return <p className={css.note}>{t('panel.worktree.loading')}</p>
  if (!overview.is_repo) return <p className={css.note}>{t('panel.worktree.empty')}</p>

  const changed = overview.status === null ? 0 : overview.status.staged + overview.status.dirty + overview.status.untracked

  return (
    <div className={css.root}>
      <div className={css.header}>
        <BranchIcon size={13} />
        <span className={css.branch}>{overview.branch || '—'}</span>
        {overview.is_worktree && (
          <span className={css.mainTag}>
            {t('panel.worktree.main')}: {overview.main}
          </span>
        )}
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.worktree.refresh')}
          title={t('panel.worktree.refresh')}
          onClick={() => void load()}
        >
          <RefreshIcon size={13} />
        </button>
      </div>
      <div className={css.status}>
        {changed === 0
          ? t('panel.worktree.clean')
          : t('panel.worktree.changed', { count: changed })}
      </div>
      <ul className={css.list}>
        {overview.worktrees.map((worktree) => (
          <li key={worktree.path} className={css.item} data-current={worktree.current || undefined}>
            <div className={css.itemHead}>
              <BranchIcon size={12} />
              <span className={css.itemBranch}>{worktree.branch || worktree.head.slice(0, 8)}</span>
              {worktree.current && (
                <span className={css.current}>
                  <CheckIcon size={11} />
                  {t('panel.worktree.current')}
                </span>
              )}
              {!worktree.current && (
                <button
                  type="button"
                  className={css.switch}
                  disabled={switching !== null || running}
                  onClick={() => void switchTo(worktree.path)}
                >
                  {switching === worktree.path
                    ? t('panel.worktree.switching')
                    : t('panel.worktree.switch')}
                </button>
              )}
            </div>
            <div className={css.path} title={worktree.path}>
              {worktree.path}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
