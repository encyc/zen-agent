/** Files tab: lazy directory tree with a single-file viewer, mirroring pi's
 * single-pane browse → open → back flow. The backend serves only paths inside
 * the runtime's project root. */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useAppStore } from '../../state/appStore'
import { FILES_REVEAL_BATCH, revealedCount } from '../../state/reveal'
import { useNoticeStore, useSettingsStore, useWorkPanelStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { useMarkdown } from '../chat/markdown'
import chatCss from '../chat/AssistantNodeView.module.css'
import type { FsListResult, FsReadResult, WorkspaceEntry, WorkspaceFile } from '../../rpc/types'
import { frameErrorNotice, type ReplyLike } from '../../rpc/frames'
import { toWorkspacePath } from './workspacePath'
import {
  ensureLang,
  highlightLines,
  langForPath,
  useHighlightVersion,
  useThemeMode,
  type ThemedToken,
} from './highlight'
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
  const handledRequest = useRef(0)
  const fileRequest = useWorkPanelStore((state) => state.fileRequest)

  const loadDir = useCallback(async (rel: string, quiet = false): Promise<boolean> => {
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<FsListResult>({ type: 'fs_list', path: rel }, REQUEST_TIMEOUT_MS)
      if (result.ok !== true) {
        if (!quiet) reportFsFailure(result)
        setDirs((prev) => ({ ...prev, [rel]: { entries: [], error: true } }))
        return false
      }
      setDirs((prev) => ({ ...prev, [rel]: { entries: result.entries ?? [] } }))
      return true
    } catch {
      setDirs((prev) => ({ ...prev, [rel]: { entries: [], error: true } }))
      return false
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

  /** Expand a reference's folder in place: every ancestor level is loaded so
   * the tree can paint it, and a path that is not a directory reports false. */
  const revealDir = async (path: string): Promise<boolean> => {
    const segments = path
      .replaceAll('\\', '/')
      .replace(/\/+$/u, '')
      .split('/')
      .filter((segment) => segment !== '' && segment !== '.')
    if (segments.length === 0 || segments.includes('..')) return false
    const prefixes = segments.map((_, index) => segments.slice(0, index + 1).join('/'))
    for (const prefix of prefixes) {
      const known = dirs[prefix]
      if (known !== undefined && known.error !== true) continue
      if (!(await loadDir(prefix, true))) return false
    }
    setExpanded((prev) => {
      const next = new Set(prev)
      for (const prefix of prefixes) next.add(prefix)
      return next
    })
    return true
  }

  const openFile = async (path: string, revealFolders = false) => {
    const seq = (generation.current += 1)
    setSelected(path)
    setFile(null)
    setError(false)
    setLoading(true)
    let result: FsReadResult | null = null
    try {
      result = await useAppStore
        .getState()
        .client()
        .send<FsReadResult>({ type: 'fs_read', path }, REQUEST_TIMEOUT_MS)
    } catch {
      result = null
    }
    if (seq !== generation.current) return
    if (result !== null && result.ok === true) {
      setFile(result.file)
      setLoading(false)
      return
    }
    // A chat reference may name a folder: opening it means expanding it in
    // the tree, not previewing bytes that will never arrive.
    if (revealFolders && (await revealDir(path)) && seq === generation.current) {
      setSelected(null)
      setLoading(false)
      return
    }
    if (seq !== generation.current) return
    if (result !== null) reportFsFailure(result)
    setError(true)
    setLoading(false)
  }

  // A chat file reference asks this viewer for one path: opening it here keeps
  // the tree state for the back button, and consuming the request stops a tab
  // switch from reopening it. A reference may be an absolute path from the
  // project root; the tree and the reveal walk are keyed by relative paths.
  useEffect(() => {
    if (fileRequest === null || fileRequest.nonce === handledRequest.current) return
    handledRequest.current = fileRequest.nonce
    useWorkPanelStore.getState().consumeFileRequest()
    const path = toWorkspacePath(fileRequest.path, useAppStore.getState().project)
    void openFile(path, true)
  }, [fileRequest, openFile])

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

/** Shiki token flags: italic 1, bold 2, underline 4. */
function tokenStyle(token: ThemedToken): CSSProperties {
  const flags = token.fontStyle ?? 0
  const parts: string[] = []
  if (flags & 1) parts.push('italic')
  if (flags & 2) parts.push('bold')
  if (flags & 4) parts.push('underline')
  const style: CSSProperties = { color: token.color }
  if (parts.length > 0) style.fontStyle = parts.join(' ')
  return style
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
  const isMarkdown = /\.(?:md|markdown)$/i.test(path)
  const markdown = useMarkdown(isMarkdown ? file.text : '', labels)
  const lines = useMemo(() => file.text.split('\n'), [file.text])
  const capped = lines.length > VIEWER_LINE_CAP
  const shown = capped ? lines.slice(0, VIEWER_LINE_CAP) : lines

  // Source files render through the lazy highlighter: the grammar loads in the
  // background, `useHighlightVersion` repaints once it is ready, and files over
  // the size guard keep the plain-text fallback.
  const lang = langForPath(path)
  if (lang !== null) ensureLang(lang)
  const version = useHighlightVersion()
  const theme = useThemeMode()
  const tokens = useMemo(
    () =>
      lang === null
        ? null
        : highlightLines(
            capped ? lines.slice(0, VIEWER_LINE_CAP).join('\n') : file.text,
            lang,
            theme,
          ),
    [capped, lines, file.text, lang, theme, version],
  )

  if (isMarkdown) {
    return (
      <div
        className={`${chatCss.markdown} ${css.markdown}`}
        dangerouslySetInnerHTML={{ __html: markdown }}
      />
    )
  }

  return (
    <div className={css.code} data-lang={lang ?? undefined}>
      {shown.map((line, index) => {
        const row = tokens?.[index]
        return (
          <div key={index} className={css.line} data-line={index + 1}>
            <span className={css.gutter}>{index + 1}</span>
            <span className={css.text}>
              {row === undefined || row.length === 0
                ? line === ''
                  ? ' '
                  : line
                : row.map((token, tokenIndex) => (
                    <span key={tokenIndex} style={tokenStyle(token)}>
                      {token.content}
                    </span>
                  ))}
            </span>
          </div>
        )
      })}
      {capped && (
        <p className={css.note}>{t('panel.files.lineCap', { count: VIEWER_LINE_CAP })}</p>
      )}
      {file.truncated && <p className={css.note}>{t('panel.files.truncated')}</p>}
    </div>
  )
}
