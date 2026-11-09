/** Files tab: lazy directory tree with a single-file viewer, mirroring pi's
 * single-pane browse → open → back flow. The backend serves only paths inside
 * the runtime's project root. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { FILES_REVEAL_BATCH, revealedCount } from '../../state/reveal'
import { useNoticeStore, useSettingsStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { useMarkdown } from '../chat/markdown'
import chatCss from '../chat/AssistantNodeView.module.css'
import type { FsListResult, FsReadResult, WorkspaceEntry, WorkspaceFile } from '../../rpc/types'
import { frameErrorNotice, type ReplyLike } from '../../rpc/frames'
import {
  ArrowLeftIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileIcon,
  FolderIcon,
} from '../icons'
import css from './FilesPanel.module.css'

const REQUEST_TIMEOUT_MS = 8000
const VIEWER_LINE_CAP = 5000

/** A failed fs request carries a `fs_error` code; show its localized copy in
 * the notice banner instead of throwing the sanitized message away. */
function reportFsFailure(result: ReplyLike): void {
  const text = frameErrorNotice(result, useSettingsStore.getState().locale)
  if (text !== null) useNoticeStore.getState().show({ kind: 'error', text })
}

interface DirState {
  entries: WorkspaceEntry[]
  error?: boolean
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function FilesPanel() {
  const t = useT()
  const [dirs, setDirs] = useState<Record<string, DirState>>({})
  // Entries mounted per directory: the first batch on open, +1 per "more".
  const [dirPages, setDirPages] = useState<Record<string, number>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<string | null>(null)
  const [file, setFile] = useState<WorkspaceFile | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const generation = useRef(0)

  const loadDir = useCallback(async (rel: string) => {
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<FsListResult>({ type: 'fs_list', path: rel }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) {
        reportFsFailure(result)
        setDirs((prev) => ({ ...prev, [rel]: { entries: [], error: true } }))
        return
      }
      setDirs((prev) => ({ ...prev, [rel]: { entries: result.entries ?? [] } }))
    } catch {
      setDirs((prev) => ({ ...prev, [rel]: { entries: [], error: true } }))
    }
  }, [])

  useEffect(() => {
    void loadDir('')
  }, [loadDir])

  const toggleDir = (rel: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(rel)) next.delete(rel)
      else next.add(rel)
      return next
    })
    if (dirs[rel] === undefined) void loadDir(rel)
  }

  const openFile = async (path: string) => {
    const seq = (generation.current += 1)
    setSelected(path)
    setFile(null)
    setError(false)
    setLoading(true)
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<FsReadResult>({ type: 'fs_read', path }, REQUEST_TIMEOUT_MS)
      if (seq !== generation.current) return
      if (result.ok !== true) {
        reportFsFailure(result)
        setError(true)
        return
      }
      setFile(result.file)
    } catch {
      if (seq === generation.current) setError(true)
    } finally {
      if (seq === generation.current) setLoading(false)
    }
  }

  const renderDir = (rel: string, depth: number): React.ReactNode => {
    const state = dirs[rel]
    if (state === undefined) {
      return (
        <div key={`${rel}:loading`} className={css.note} style={{ paddingLeft: 12 + depth * 14 }}>
          {t('panel.files.loading')}
        </div>
      )
    }
    if (state.entries.length === 0) {
      return (
        <div key={`${rel}:empty`} className={css.note} style={{ paddingLeft: 12 + depth * 14 }}>
          {state.error === true ? t('panel.files.error') : t('panel.files.empty')}
        </div>
      )
    }
    const shown = revealedCount(
      state.entries.length,
      dirPages[rel] ?? 1,
      FILES_REVEAL_BATCH,
    )
    const hidden = state.entries.length - shown
    const rows = state.entries.slice(0, shown).map((entry) => {
      if (entry.kind === 'dir') {
        const open = expanded.has(entry.path)
        return (
          <div key={entry.path}>
            <button
              type="button"
              className={css.row}
              style={{ paddingLeft: 12 + depth * 14 }}
              onClick={() => toggleDir(entry.path)}
            >
              <span className={css.caret}>
                {open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
              </span>
              <FolderIcon size={14} />
              <span className={css.name}>{entry.name}</span>
            </button>
            {open && renderDir(entry.path, depth + 1)}
          </div>
        )
      }
      return (
        <button
          key={entry.path}
          type="button"
          className={css.row}
          data-active={selected === entry.path || undefined}
          data-file-path={entry.path}
          style={{ paddingLeft: 12 + depth * 14 + 18 }}
          title={entry.path}
          onClick={() => void openFile(entry.path)}
        >
          <FileIcon size={14} />
          <span className={css.name}>{entry.name}</span>
        </button>
      )
    })
    if (hidden === 0) return rows
    return [
      ...rows,
      <button
        key={`${rel}:more`}
        type="button"
        className={css.more}
        style={{ paddingLeft: 12 + depth * 14 }}
        onClick={() =>
          setDirPages((value) => ({ ...value, [rel]: (value[rel] ?? 1) + 1 }))
        }
      >
        {t('sidebar.more', { count: hidden })}
      </button>,
    ]
  }

  if (selected !== null) {
    return (
      <div className={css.viewer}>
        <div className={css.viewerHeader}>
          <button
            type="button"
            className={css.iconButton}
            aria-label={t('panel.files.back')}
            title={t('panel.files.back')}
            onClick={() => {
              generation.current += 1
              setSelected(null)
              setFile(null)
              setError(false)
              setLoading(false)
            }}
          >
            <ArrowLeftIcon size={14} />
          </button>
          <span className={css.path} title={selected}>
            {selected}
          </span>
          {file !== null && <span className={css.size}>{formatSize(file.size)}</span>}
        </div>
        <div className={css.viewerBody} data-selectable>
          {error ? (
            <p className={css.note}>{t('panel.files.error')}</p>
          ) : loading || file === null ? (
            <p className={css.note}>{t('panel.files.loading')}</p>
          ) : file.kind === 'text' ? (
            <TextFile path={selected} file={file} />
          ) : file.kind === 'image' ? (
            <div className={css.image}>
              <img src={file.data_url} alt={selected} />
            </div>
          ) : (
            <p className={css.note}>
              {file.kind === 'tooLarge' ? t('panel.files.tooLarge') : t('panel.files.binary')}
            </p>
          )}
        </div>
      </div>
    )
  }

  return <div className={css.tree}>{renderDir('', 0)}</div>
}

function TextFile({
  path,
  file,
}: {
  path: string
  file: Extract<WorkspaceFile, { kind: 'text' }>
}) {
  const t = useT()
  const labels = { copy: t('chat.copy'), copied: t('chat.copied') }
  const markdown = useMarkdown(/\.(?:md|markdown)$/i.test(path) ? file.text : '', labels)
  const lines = file.text.split('\n')
  const capped = lines.length > VIEWER_LINE_CAP

  if (/\.(?:md|markdown)$/i.test(path)) {
    return (
      <div
        className={`${chatCss.markdown} ${css.markdown}`}
        dangerouslySetInnerHTML={{ __html: markdown }}
      />
    )
  }

  return (
    <div className={css.code}>
      {(capped ? lines.slice(0, VIEWER_LINE_CAP) : lines).map((line, index) => (
        <div key={index} className={css.line} data-line={index + 1}>
          <span className={css.gutter}>{index + 1}</span>
          <span className={css.text}>{line === '' ? ' ' : line}</span>
        </div>
      ))}
      {capped && (
        <p className={css.note}>{t('panel.files.lineCap', { count: VIEWER_LINE_CAP })}</p>
      )}
      {file.truncated && <p className={css.note}>{t('panel.files.truncated')}</p>}
    </div>
  )
}
