/** Branch section: the current branch and the repository's worktrees, newest
 * branch tip first. Collapsible and height-capped — a checkout with dozens of
 * worktrees must not push Changes or Graph off screen. A row click previews
 * that worktree's changes read-only; it never switches the project, so the
 * current row is marked and every other row is just a preview target. The
 * collapsed state is remembered per machine, like the rest of the work rail's
 * layout. */

import { useCallback, useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore.ts'
import { useT } from '../../i18n/locale.ts'
import { useSettingsStore } from '../../state/stores.ts'
import type { GitWorktreesResult, WorktreeOverview } from '../../rpc/types.ts'
import type { GitScope } from '../../state/gitStore.ts'
import { frameError } from '../../rpc/frames.ts'
import { formatAge } from '../format.ts'
import {
  BranchIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  RefreshIcon,
} from '../icons'
import { sortWorktrees } from './worktrees.ts'
import css from './GitBranchSection.module.css'

const COLLAPSED_KEY = 'zen.git.branches.collapsed'
const REQUEST_TIMEOUT_MS = 8000

interface GitBranchSectionProps {
  branch: string
  scope: GitScope | null
  onSelect(scope: GitScope | null): void
  onRefresh(): void
}

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

export function GitBranchSection({
  branch,
  scope,
  onSelect,
  onRefresh,
}: GitBranchSectionProps) {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const [collapsed, setCollapsed] = useState(loadCollapsed)
  const [overview, setOverview] = useState<WorktreeOverview | null>(null)
  const [failed, setFailed] = useState(false)
  const worktrees = overview?.is_repo === true ? sortWorktrees(overview.worktrees) : []
  const currentBranch = branch || overview?.branch || '—'

  const load = useCallback(async () => {
    setFailed(false)
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitWorktreesResult>({ type: 'git_worktrees' }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) throw new Error(frameError(result) ?? 'git_worktrees failed')
      setOverview(result.worktree)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const toggle = () => {
    setCollapsed((previous) => {
      const next = !previous
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
      } catch {
        // A locked-down webview can refuse storage; the toggle still works.
      }
      return next
    })
  }

  const label = collapsed ? t('panel.git.expandBranch') : t('panel.git.collapseBranch')

  return (
    <section className={css.root} data-git-branches="">
      <div className={css.header}>
        <button
          type="button"
          className={css.toggle}
          aria-expanded={!collapsed}
          aria-label={label}
          title={label}
          onClick={toggle}
        >
          {collapsed ? <ChevronRightIcon size={12} /> : <ChevronDownIcon size={12} />}
          <BranchIcon size={12} />
          <span className={css.sectionTitle}>{t('panel.git.branches')}</span>
          <span className={css.branch} title={currentBranch}>
            {currentBranch}
          </span>
          {worktrees.length > 0 && <span className={css.count}>{worktrees.length}</span>}
        </button>
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.git.refresh')}
          title={t('panel.git.refresh')}
          onClick={() => {
            void load()
            onRefresh()
          }}
        >
          <RefreshIcon size={13} />
        </button>
      </div>
      {!collapsed && (
        <div className={css.body}>
          {worktrees.length > 0 && (
            <ul className={css.list} data-worktrees="">
              {worktrees.map((worktree) => {
                const scoped = scope?.path === worktree.path
                const active = worktree.current ? scope === null : scoped
                return (
                  <li
                    key={worktree.path}
                    className={css.row}
                    data-current={worktree.current || undefined}
                    data-scoped={scoped || undefined}
                    data-worktree={worktree.branch || worktree.head}
                    title={worktree.path}
                  >
                    <button
                      type="button"
                      className={css.pick}
                      aria-pressed={active}
                      title={t('panel.git.previewHint')}
                      onClick={() =>
                        onSelect(
                          worktree.current || scoped
                            ? null
                            : {
                                path: worktree.path,
                                branch:
                                  worktree.branch || worktree.head.slice(0, 8),
                              },
                        )
                      }
                    >
                      <span className={css.name}>
                        {worktree.branch || worktree.head.slice(0, 8)}
                      </span>
                      <span className={css.age}>{formatAge(worktree.timestamp, locale)}</span>
                    </button>
                    {worktree.current && (
                      <span className={css.current}>
                        <CheckIcon size={11} />
                        {t('panel.git.current')}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {failed && <p className={css.note}>{t('panel.git.worktreeError')}</p>}
          {overview === null && !failed && <p className={css.note}>{t('panel.git.loading')}</p>}
          {overview !== null && !overview.is_repo && (
            <p className={css.note}>{t('panel.git.empty')}</p>
          )}
        </div>
      )}
    </section>
  )
}
