/** Git tab: the collapsible branch/worktree section, the three-group changes
 * list, the commit graph and a unified-diff detail pane. Wide rail: two
 * columns (branch + changes + graph above the scrolling list, detail on the
 * right); narrow rail: list → detail → back, the Files tab's single-pane
 * flow. Untracked rows render the backend's synthetic whole-file-new diff;
 * binary/clean files get placeholders. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore.ts'
import { useChatStore } from '../../state/chatStore.ts'
import { diffKey, useGitStore } from '../../state/gitStore.ts'
import type { GitScope } from '../../state/gitStore.ts'
import type { GitFileChange, GitShowFile, GitShowPayload } from '../../rpc/types.ts'
import { useWorkPanelStore } from '../../state/stores.ts'
import { useT } from '../../i18n/locale.ts'
import {
  ArrowLeftIcon,
  BranchIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileIcon,
  ListIcon,
  TreeIcon,
} from '../icons'
import { groupChanges, isSessionFile, sessionPaths } from './gitChanges.ts'
import { FileTreeList } from './FileTreeList.tsx'
import { GitBranchSection } from './GitBranchSection.tsx'
import { GitGraphSection } from './GitGraphSection.tsx'
import { fileBasename } from './review.ts'
import { ViewToggle } from './ViewToggle.tsx'
import { readViewPref, writeViewPref } from './viewPrefs.ts'
import { DiffBlock } from './DiffBlock'
import css from './GitPanel.module.css'

const NARROW_PX = 520
const CHANGES_VIEW_KEY = 'zen.git.changesView'
const COMMIT_VIEW_KEY = 'zen.git.commitView'

type ChangesView = 'tree' | 'list'
type CommitView = 'tree' | 'list'

const GROUPS = [
  { state: 'staged', label: 'panel.git.staged' },
  { state: 'unstaged', label: 'panel.git.unstaged' },
  { state: 'untracked', label: 'panel.git.untracked' },
] as const

type GroupState = (typeof GROUPS)[number]['state']

const EMPTY_GROUPS: Record<GroupState, ReadonlySet<string>> = {
  staged: new Set(),
  unstaged: new Set(),
  untracked: new Set(),
}

export function GitPanel({ width }: { width: number }) {
  const t = useT()
  const narrow = width < NARROW_PX
  const changes = useGitStore((state) => state.changes)
  const failed = useGitStore((state) => state.failed)
  const selected = useGitStore((state) => state.selected)
  const scope = useGitStore((state) => state.scope)
  const setScope = useGitStore((state) => state.setScope)
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

  const grouped = useMemo(() => groupChanges(changes?.files ?? []), [changes])
  const session = useMemo(() => sessionPaths(nodes, project), [nodes, project])

  useEffect(() => {
    void loadChanges()
    void loadLog()
  }, [loadChanges, loadLog])

  // The left column is the scroll container. A sentinel near its bottom
  // asks for the next graph page — an observer, not a scroll handler, so the
  // prefetch also fires when the tail is already on screen (short lists,
  // programmatic scrolls).
  const sideRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const sentinel = sentinelRef.current
    const root = sideRef.current
    if (sentinel === null || root === null || !hasMoreLog) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadMoreLog()
      },
      { root, rootMargin: '200px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasMoreLog, loadMoreLog])

  const fileSel = selected?.kind === 'file' ? selected : null
  const shown: GitShowPayload | null =
    selected?.kind === 'commit' ? showFor(selected.hash) : null
  const selectedDiffKey =
    fileSel !== null ? diffKey(scope, fileSel.path, fileSel.staged) : ''
  const diff = fileSel !== null ? diffFor(selectedDiffKey) : null
  const entry =
    fileSel !== null ? changes?.files.find((file) => file.path === fileSel.path) ?? null : null

  // Commit files start folded: the first file opens so a click on the graph
  // still lands on a diff, the rest stay as a scannable file list. The set
  // resets whenever another commit (or its freshly fetched payload) arrives.
  const commitHash = selected?.kind === 'commit' ? selected.hash : null
  const [openFiles, setOpenFiles] = useState<ReadonlySet<string>>(() => new Set())
  useEffect(() => {
    if (commitHash === null) return
    const files = showFor(commitHash)?.files ?? []
    setOpenFiles(new Set(files.length > 0 ? [files[0].path] : []))
  }, [commitHash, shown, showFor])
  const allOpen =
    shown !== null &&
    shown.files.length > 0 &&
    shown.files.every((file) => openFiles.has(file.path))
  const toggleFile = (path: string) => {
    setOpenFiles((previous) => {
      const next = new Set(previous)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }
  const toggleAll = () => {
    if (allOpen) {
      setOpenFiles(new Set())
      return
    }
    setOpenFiles(new Set(shown?.files.map((file) => file.path) ?? []))
    // Expanding every diff must also reopen the folders that hide them.
    setCollapsedCommits(new Set())
  }

  // VSCode's two display modes: the Changes list and the commit's file list
  // remember tree/list independently. Each changes group keeps its own folder
  // collapse set too, so folding `desktop` under Changes leaves the same
  // `desktop` folder under Untracked open.
  const [changesView, setChangesView] = useState<ChangesView>(() =>
    readViewPref(CHANGES_VIEW_KEY, ['tree', 'list'] as const, 'tree'),
  )
  const [commitView, setCommitView] = useState<CommitView>(() =>
    readViewPref(COMMIT_VIEW_KEY, ['tree', 'list'] as const, 'tree'),
  )
  const [collapsedChanges, setCollapsedChanges] =
    useState<Record<GroupState, ReadonlySet<string>>>(EMPTY_GROUPS)
  const [collapsedGroups, setCollapsedGroups] = useState<ReadonlySet<GroupState>>(
    () => new Set(),
  )
  const [collapsedCommits, setCollapsedCommits] = useState<ReadonlySet<string>>(
    () => new Set(),
  )
  const chooseChangesView = (view: ChangesView) => {
    setChangesView(view)
    writeViewPref(CHANGES_VIEW_KEY, view)
  }
  const chooseCommitView = (view: CommitView) => {
    setCommitView(view)
    writeViewPref(COMMIT_VIEW_KEY, view)
  }

  if (changes !== null && !changes.is_repo) {
    return <p className={css.note}>{t('panel.git.empty')}</p>
  }
  if (changes === null && !failed) {
    return <p className={css.note}>{t('panel.git.loading')}</p>
  }

  const list = (
    <div
      ref={sideRef}
      className={css.side}
      data-git-side
      data-embed=""
      data-selectable=""
      onScroll={(event) => {
        const el = event.currentTarget
        if (el.scrollTop + el.clientHeight >= el.scrollHeight - 24) void loadMoreLog()
      }}
    >
      <GitBranchSection
        branch={changes?.branch ?? ''}
        scope={scope}
        onSelect={(next: GitScope | null) => setScope(next)}
        onRefresh={() => {
          void loadChanges()
          void loadLog()
        }}
      />
      {scope !== null && (
        <div className={css.scopeBar} data-git-scope="">
          <span className={css.scopeText}>
            {t('panel.git.previewing', { branch: scope.branch })}
          </span>
          <button
            type="button"
            className={css.scopeBack}
            onClick={() => setScope(null)}
          >
            {t('panel.git.previewBack')}
          </button>
        </div>
      )}
      {failed && <p className={css.note}>{t('panel.git.failed')}</p>}

      {changes !== null && changes.files.length > 0 && (
        <div className={css.toolbar} data-git-changes-toolbar="">
          <span className={css.toolbarLabel}>{t('panel.git.workingTree')}</span>
          <span className={css.toolbarCount}>{changes.files.length}</span>
          <span className={css.toolbarSpacer} />
          <ViewToggle<ChangesView>
            label={t('panel.git.viewMode')}
            value={changesView}
            onChange={chooseChangesView}
            options={[
              {
                value: 'tree',
                label: t('panel.git.treeView'),
                icon: <TreeIcon size={12} />,
              },
              {
                value: 'list',
                label: t('panel.git.listView'),
                icon: <ListIcon size={12} />,
              },
            ]}
          />
        </div>
      )}

      {GROUPS.map(({ state, label }) => {
        const files = grouped[state]
        if (files.length === 0) return null
        const folded = collapsedGroups.has(state)
        return (
          <section key={state} className={css.group} data-group={state}>
            <button
              type="button"
              className={css.groupHeader}
              data-group-toggle=""
              aria-expanded={!folded}
              aria-label={t(label)}
              onClick={() =>
                setCollapsedGroups((previous) => toggleIn(previous, state))
              }
            >
              {folded ? <ChevronRightIcon size={12} /> : <ChevronDownIcon size={12} />}
              <span className={css.groupLabel}>{t(label)}</span>
              <span className={css.groupCount}>{files.length}</span>
            </button>
            {!folded &&
              (changesView === 'tree' ? (
                <FileTreeList
                  files={files}
                  collapsed={collapsedChanges[state]}
                  onToggleDir={(path) =>
                    setCollapsedChanges((previous) => ({
                      ...previous,
                      [state]: toggleIn(previous[state], path),
                    }))
                  }
                  renderFile={(file, depth) => (
                    <FileRow
                      key={`${file.state}:${file.path}`}
                      file={file}
                      label={fileBasename(file.path)}
                      indent={depth}
                      selected={
                        fileSel?.path === file.path &&
                        fileSel.staged === (file.state === 'staged')
                      }
                      session={scope === null && isSessionFile(file.path, session)}
                      onOpen={() => void openFile(file.path, file.state === 'staged')}
                    />
                  )}
                />
              ) : (
                files.map((file) => (
                  <FileRow
                    key={`${file.state}:${file.path}`}
                    file={file}
                    selected={
                      fileSel?.path === file.path &&
                      fileSel.staged === (file.state === 'staged')
                    }
                    session={scope === null && isSessionFile(file.path, session)}
                    onOpen={() => void openFile(file.path, file.state === 'staged')}
                  />
                ))
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
      {graph !== null && graph.length > 0 && (
        <GitGraphSection commits={graph} branch={changes?.branch ?? ''} />
      )}
      {graph !== null && graph.length === 0 && (
        <p className={css.note}>{t('panel.git.graphEmpty')}</p>
      )}
      {graph !== null && graph.length > 0 && !hasMoreLog && (
        <p className={css.moreRow}>{t('panel.git.graphEnd')}</p>
      )}
      <div ref={sentinelRef} aria-hidden="true" style={{ minHeight: 1 }} />
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

  // Both branches must be null when inactive: `false ?? fileDetail` is
  // `false`, which would blank the pane instead of falling through.
  const commitDetail =
    selected?.kind === 'commit' ? (
      <div className={css.detail} data-embed="" data-selectable="">
        <div className={css.detailHeader}>
          {narrow && back}
          <BranchIcon size={13} />
          <span className={css.detailName}>{shown?.subject ?? t('panel.git.loading')}</span>
          <span className={css.detailPath}>{selected.hash.slice(0, 8)}</span>
        </div>
        {shown !== null && shown.files.length > 0 && (
          <div className={css.toolbar} data-git-commit-toolbar="">
            <span className={css.toolbarLabel}>
              {t('panel.git.commitFiles', { count: shown.files.length })}
            </span>
            <span className={css.toolbarSpacer} />
            {shown.files.length > 1 && (
              <>
                <ViewToggle<CommitView>
                  label={t('panel.git.fileView')}
                  value={commitView}
                  onChange={chooseCommitView}
                  options={[
                    {
                      value: 'tree',
                      label: t('panel.git.treeView'),
                      icon: <TreeIcon size={12} />,
                    },
                    {
                      value: 'list',
                      label: t('panel.git.listView'),
                      icon: <ListIcon size={12} />,
                    },
                  ]}
                />
                <button type="button" className={css.foldAll} onClick={toggleAll}>
                  {allOpen ? t('panel.git.collapseAll') : t('panel.git.expandAll')}
                </button>
              </>
            )}
          </div>
        )}
        <div className={css.detailBody}>
          {shown === null ? (
            <p className={css.note}>{t('panel.git.loading')}</p>
          ) : !shown.found ? (
            <p className={css.note}>{t('panel.git.missing')}</p>
          ) : shown.files.length === 0 ? (
            <p className={css.note}>{t('panel.git.emptyCommit')}</p>
          ) : commitView === 'tree' ? (
            <FileTreeList
              files={shown.files}
              collapsed={collapsedCommits}
              onToggleDir={(path) =>
                setCollapsedCommits((previous) => toggleIn(previous, path))
              }
              renderFile={(file, depth) => (
                <CommitFile
                  key={file.path}
                  file={file}
                  label={fileBasename(file.path)}
                  indent={depth}
                  open={openFiles.has(file.path)}
                  onToggle={() => toggleFile(file.path)}
                />
              )}
            />
          ) : (
            shown.files.map((file) => (
              <CommitFile
                key={file.path}
                file={file}
                open={openFiles.has(file.path)}
                onToggle={() => toggleFile(file.path)}
              />
            ))
          )}
        </div>
      </div>
    ) : null

  const fileDetail =
    fileSel !== null ? (
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
          {scope === null && (
            <button
              type="button"
              className={css.openInFiles}
              onClick={() => useWorkPanelStore.getState().openFile(fileSel.path)}
            >
              {t('panel.git.openInFiles')}
            </button>
          )}
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
                  {t('panel.git.truncated')}
                  {scope === null && ` · ${t('panel.git.openInFiles')}`}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    ) : null

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
  indent = 0,
  label,
  onOpen,
}: {
  file: GitFileChange
  selected: boolean
  session: boolean
  /** Tree depth; the caller also supplies the basename as `label`. */
  indent?: number
  label?: string
  onOpen(): void
}) {
  return (
    <button
      type="button"
      className={css.row}
      data-state={file.state}
      data-selected={selected || undefined}
      aria-label={file.path}
      title={file.path}
      style={{ paddingLeft: 18 + indent * 12 }}
      onClick={onOpen}
    >
      <span className={css.rowPath}>{label ?? file.path}</span>
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

function CommitFile({
  file,
  open,
  indent = 0,
  label,
  onToggle,
}: {
  file: GitShowFile
  open: boolean
  /** Tree depth; the caller also supplies the basename as `label`. */
  indent?: number
  label?: string
  onToggle(): void
}) {
  return (
    <section className={css.commitFile} data-open={open || undefined}>
      <button
        type="button"
        className={css.commitFileHeader}
        aria-expanded={open}
        aria-label={file.path}
        title={file.path}
        style={{ paddingLeft: 8 + indent * 12 }}
        onClick={onToggle}
      >
        {open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
        <FileIcon size={12} />
        <span className={css.rowPath}>{label ?? file.path}</span>
        <span className={css.counts}>
          <span className={css.added}>+{file.additions}</span>{' '}
          <span className={css.removed}>−{file.removals}</span>
        </span>
      </button>
      {open && (
        <div className={css.commitFileBody}>
          <DiffBlock diff={file.diff} />
        </div>
      )}
    </section>
  )
}

function toggleIn<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}
