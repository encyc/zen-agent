/** Git tab: the branch/worktree switcher, the three-group changes list and a
 * unified-diff detail pane. Wide rail: two columns (sticky branch section
 * above a scrolling list); narrow rail: list → detail → back, the Files tab's
 * single-pane flow. Untracked rows render the backend's synthetic
 * whole-file-new diff; binary/clean files get placeholders. */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAppStore } from '../../state/appStore.ts'
import { useChatStore } from '../../state/chatStore.ts'
import { useGitStore } from '../../state/gitStore.ts'
import type { GitShowPayload } from '../../rpc/types.ts'
import { useStatusStore, useWorkPanelStore } from '../../state/stores.ts'
import { useT } from '../../i18n/locale.ts'
import type { GitFileChange, GitWorktreesResult, WorktreeOverview } from '../../rpc/types.ts'
import { frameError } from '../../rpc/frames.ts'
import { ArrowLeftIcon, BranchIcon, CheckIcon, FileIcon, RefreshIcon } from '../icons'
import { groupChanges, isSessionFile, sessionPaths } from './gitChanges.ts'
import { GitGraphSection } from './GitGraphSection.tsx'
import { fileBasename } from './review.ts'
import { DiffBlock } from './DiffBlock'
import css from './GitPanel.module.css'

const REQUEST_TIMEOUT_MS = 8000
const NARROW_PX = 520

const GROUPS = [
  { state: 'staged', label: 'panel.git.staged' },
  { state: 'unstaged', label: 'panel.git.unstaged' },
  { state: 'untracked', label: 'panel.git.untracked' },
] as const

export function GitPanel({ width }: { width: number }) {
  const t = useT()
  const narrow = width < NARROW_PX
  const changes = useGitStore((state) => state.changes)
  const failed = useGitStore((state) => state.failed)
  const selected = useGitStore((state) => state.selected)
  const openFile = useGitStore((state) => state.openFile)
  const loadChanges = useGitStore((state) => state.loadChanges)
  const loadLog = useGitStore((state) => state.loadLog)
  const loadMoreLog = useGitStore((state) => state.loadMoreLog)
  const hasMoreLog = useGitStore((state) => state.hasMoreLog)
  const graph = useGitStore((state) => state.log)
  const diffFor = useGitStore((state) => state.diffFor)
  const showFor = useGitStore((state) => state.showFor)
  const nodes = useChatStore((state) => state.nodes)
  const project = useAppStore((state) => state.project)
  const running = useStatusStore((state) => state.status?.running === true)

  const grouped = useMemo(() => groupChanges(changes?.files ?? []), [changes])
  const session = useMemo(() => sessionPaths(nodes, project), [nodes, project])

  useEffect(() => {
    void loadChanges()
    void loadLog()
  }, [loadChanges, loadLog])

  // The left column is the scroll container; its bottom edge asks for the
  // next graph page.
  const onSideScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget
    if (!hasMoreLog || el.scrollTop + el.clientHeight < el.scrollHeight - 24) return
    void loadMoreLog()
  }

  // The branch section keeps WorktreePanel's fetch: worktrees + switch, with
  // the same guard that a switch restarts the runtime and must not race a run.
  const [overview, setOverview] = useState<WorktreeOverview | null>(null)
  const [worktreeFailed, setWorktreeFailed] = useState(false)
  const [switching, setSwitching] = useState<string | null>(null)

  const loadOverview = useCallback(async () => {
    setWorktreeFailed(false)
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<GitWorktreesResult>({ type: 'git_worktrees' }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) throw new Error(frameError(result) ?? 'git_worktrees failed')
      setOverview(result.worktree)
    } catch {
      setWorktreeFailed(true)
    }
  }, [])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const switchTo = async (path: string) => {
    if (switching !== null || running) return
    setSwitching(path)
    try {
      await useAppStore.getState().openProject(path)
    } finally {
      setSwitching(null)
    }
  }

  const fileSel = selected?.kind === 'file' ? selected : null
  const shown: GitShowPayload | null =
    selected?.kind === 'commit' ? showFor(selected.hash) : null
  const diffKey = fileSel !== null ? `${fileSel.path}:${fileSel.staged ? 's' : 'w'}` : ''
  const diff = fileSel !== null ? diffFor(diffKey) : null
  const entry =
    fileSel !== null ? changes?.files.find((file) => file.path === fileSel.path) ?? null : null

  if (changes !== null && !changes.is_repo) {
    return <p className={css.note}>{t('panel.git.empty')}</p>
  }
  if (changes === null && !failed) {
    return <p className={css.note}>{t('panel.git.loading')}</p>
  }

  const list = (
    <div className={css.side} data-embed="" data-selectable="" onScroll={onSideScroll}>
      <div className={css.branchBar}>
        <BranchIcon size={13} />
        <span className={css.branch}>{overview?.branch || changes?.branch || '—'}</span>
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.git.refresh')}
          title={t('panel.git.refresh')}
          onClick={() => {
            void loadChanges()
            void loadOverview()
          }}
        >
          <RefreshIcon size={13} />
        </button>
      </div>
      {overview !== null && overview.is_repo && (
        <ul className={css.worktrees}>
          {overview.worktrees.map((worktree) => (
            <li key={worktree.path} className={css.worktree} data-current={worktree.current || undefined}>
              <span className={css.worktreeBranch}>{worktree.branch || worktree.head.slice(0, 8)}</span>
              {worktree.current ? (
                <span className={css.current}>
                  <CheckIcon size={11} />
                  {t('panel.git.current')}
                </span>
              ) : (
                <button
                  type="button"
                  className={css.switch}
                  disabled={switching !== null || running}
                  onClick={() => void switchTo(worktree.path)}
                >
                  {switching === worktree.path ? t('panel.git.switching') : t('panel.git.switch')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {worktreeFailed && <p className={css.note}>{t('panel.git.worktreeError')}</p>}
      {failed && <p className={css.note}>{t('panel.git.failed')}</p>}

      {GROUPS.map(({ state, label }) => {
        const files = grouped[state]
        if (files.length === 0) return null
        return (
          <section key={state} className={css.group}>
            <div className={css.groupHeader}>
              <span>{t(label)}</span>
              <span className={css.groupCount}>{files.length}</span>
            </div>
            {files.map((file) => (
              <FileRow
                key={`${file.state}:${file.path}`}
                file={file}
                selected={fileSel?.path === file.path && fileSel.staged === (file.state === 'staged')}
                session={isSessionFile(file.path, session)}
                onOpen={() => void openFile(file.path, file.state === 'staged')}
              />
            ))}
          </section>
        )
      })}
      {changes !== null && changes.untracked_more > 0 && (
        <p className={css.moreRow}>{t('panel.git.untrackedMore', { count: changes.untracked_more })}</p>
      )}
      {changes !== null && changes.files.length === 0 && (
        <p className={css.note}>{t('panel.git.clean')}</p>
      )}
      {graph !== null && graph.length > 0 && <GitGraphSection commits={graph} />}
      {graph !== null && !hasMoreLog && <p className={css.moreRow}>{t('panel.git.graphEnd')}</p>}
    </div>
  )

  const back = (
    <button
      type="button"
      className={css.iconButton}
      aria-label={t('panel.git.back')}
      title={t('panel.git.back')}
      onClick={() => useGitStore.setState({ selected: null })}
    >
      <ArrowLeftIcon size={14} />
    </button>
  )

  const commitDetail = selected?.kind === 'commit' && (
    <div className={css.detail} data-embed="" data-selectable="">
      <div className={css.detailHeader}>
        {narrow && back}
        <BranchIcon size={13} />
        <span className={css.detailName}>{shown?.subject ?? t('panel.git.loading')}</span>
        <span className={css.detailPath}>{selected.hash.slice(0, 8)}</span>
        {shown != null && (
          <span className={css.counts}>
            {t('panel.git.commitFiles', { count: shown.files.length })}
          </span>
        )}
      </div>
      <div className={css.detailBody}>
        {shown === null ? (
          <p className={css.note}>{t('panel.git.loading')}</p>
        ) : !shown.found ? (
          <p className={css.note}>{t('panel.git.missing')}</p>
        ) : shown.files.length === 0 ? (
          <p className={css.note}>{t('panel.git.emptyCommit')}</p>
        ) : (
          shown.files.map((file) => (
            <section key={file.path} className={css.commitFile}>
              <div className={css.commitFileHeader}>
                <span className={css.rowPath}>{file.path}</span>
                <span className={css.counts}>
                  <span className={css.added}>+{file.additions}</span>{' '}
                  <span className={css.removed}>−{file.removals}</span>
                </span>
              </div>
              <DiffBlock diff={file.diff} />
            </section>
          ))
        )}
      </div>
    </div>
  )

  const fileDetail = fileSel !== null && (
    <div className={css.detail} data-embed="" data-selectable="">
      <div className={css.detailHeader}>
        {narrow && back}
        <FileIcon size={13} />
        <span className={css.detailName}>{fileBasename(fileSel.path)}</span>
        <span className={css.detailPath}>{fileSel.path}</span>
        {entry != null && entry.plus != null && (
          <span className={css.counts}>
            <span className={css.added}>+{entry.plus}</span>{' '}
            <span className={css.removed}>−{entry.minus}</span>
          </span>
        )}
        <button
          type="button"
          className={css.openInFiles}
          onClick={() => useWorkPanelStore.getState().openFile(fileSel.path)}
        >
          {t('panel.git.openInFiles')}
        </button>
      </div>
      <div className={css.detailBody}>
        {diff === null ? (
          <p className={css.note}>{t('panel.git.loading')}</p>
        ) : diff.binary ? (
          <p className={css.note}>{t('panel.git.binary')}</p>
        ) : diff.missing ? (
          <p className={css.note}>{t('panel.git.missing')}</p>
        ) : (
          <>
            <DiffBlock diff={diff.text} />
            {diff.truncated && (
              <p className={css.footnote}>
                {t('panel.git.truncated')} · {t('panel.git.openInFiles')}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )

  const detail = commitDetail ?? fileDetail

  if (narrow) {
    return <div className={css.root}>{selected !== null ? detail : list}</div>
  }
  return (
    <div className={css.root} data-wide="">
      {list}
      <div className={css.detailCol}>{detail ?? <p className={css.note}>{t('panel.git.pick')}</p>}</div>
    </div>
  )
}

function FileRow({
  file,
  selected,
  session,
  onOpen,
}: {
  file: GitFileChange
  selected: boolean
  session: boolean
  onOpen(): void
}) {
  return (
    <button
      type="button"
      className={css.row}
      data-state={file.state}
      data-selected={selected || undefined}
      title={file.path}
      onClick={onOpen}
    >
      <span className={css.rowPath}>{file.path}</span>
      {session && <span className={css.badge}>·</span>}
      {file.plus != null && (
        <span className={css.counts}>
          <span className={css.added}>+{file.plus}</span>{' '}
          <span className={css.removed}>−{file.minus}</span>
        </span>
      )}
    </button>
  )
}
