/** Memory tab: the project's durable notes (`.zenagent/memory/`) as a
 * list → rendered note → source editor, writing through the same MemoryStore
 * the tools use. Every save and delete is journaled; the model sees the
 * change on its next turn. */

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useAppStore } from '../../state/appStore'
import { useT } from '../../i18n/locale'
import { filterMemoryEntries, sortMemoryEntries } from '../../state/memory'
import { MEMORY_REVEAL_BATCH, revealedCount } from '../../state/reveal'
import type {
  MemoryEntry,
  MemoryEntryResult,
  MemoryListResult,
} from '../../rpc/types'
import { frameError, type ReplyLike } from '../../rpc/frames'
import { useMarkdown } from '../chat/markdown'
import chatCss from '../chat/AssistantNodeView.module.css'
import {
  ArrowLeftIcon,
  PencilIcon,
  PlusIcon,
  RefreshIcon,
  SearchIcon,
  SparkIcon,
  TrashIcon,
} from '../icons'
import css from './MemoryPanel.module.css'

const REQUEST_TIMEOUT_MS = 8000

type PanelMode =
  | { kind: 'list' }
  | { kind: 'view'; entryId: string }
  | { kind: 'edit'; entryId: string | null }

async function sendMemory<T extends ReplyLike>(payload: Record<string, unknown>): Promise<T> {
  const frame = await useAppStore
    .getState()
    .client()
    .send<T>({ type: 'memory', ...payload }, REQUEST_TIMEOUT_MS)
  const error = frameError(frame, 'memory failed')
  if (error !== null) throw new Error(error)
  // Fail closed: anything that is not an explicit success frame is a failure,
  // even if it is too malformed for `frameError` to describe.
  if (frame.type !== 'result' || frame.ok !== true) throw new Error('memory failed')
  return frame
}

function displayTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}

function snippet(body: string): string {
  const line = body
    .split('\n')
    .map((value) => value.trim())
    .find((value) => value !== '')
  if (line === undefined) return ''
  return line.length > 120 ? `${line.slice(0, 119)}…` : line
}

export function MemoryPanel() {
  const t = useT()
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<PanelMode>({ kind: 'list' })
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  // Notes mounted in the list: the first batch on open, +1 per "more".
  const [revealPages, setRevealPages] = useState(1)
  const generation = useRef(0)

  const viewed =
    mode.kind === 'view'
      ? entries.find((entry) => entry.id === mode.entryId) ?? null
      : null
  const markdown = useMarkdown(viewed?.body ?? '', {
    copy: t('chat.copy'),
    copied: t('chat.copied'),
  })

  const load = useCallback(async () => {
    const seq = (generation.current += 1)
    setLoading(true)
    setError(false)
    try {
      const frame = await sendMemory<MemoryListResult>({ op: 'list' })
      if (seq !== generation.current) return
      setEntries(sortMemoryEntries(frame.entries ?? []))
    } catch {
      if (seq === generation.current) setError(true)
    } finally {
      if (seq === generation.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const showList = () => {
    setMode({ kind: 'list' })
    setConfirmDelete(false)
    setSaveError(false)
  }

  const openEntry = (entry: MemoryEntry) => {
    setMode({ kind: 'view', entryId: entry.id })
    setConfirmDelete(false)
    setSaveError(false)
  }

  const startEdit = (entry: MemoryEntry | null) => {
    setMode({ kind: 'edit', entryId: entry?.id ?? null })
    setTitle(entry?.title ?? '')
    setBody(entry?.body ?? '')
    setDirty(false)
    setSaveError(false)
    setConfirmDelete(false)
  }

  const cancelEdit = () => {
    if (mode.kind !== 'edit') return
    setSaveError(false)
    if (mode.entryId === null) showList()
    else setMode({ kind: 'view', entryId: mode.entryId })
  }

  const canSave = !saving && dirty && (title.trim() !== '' || body.trim() !== '')

  const save = async () => {
    if (mode.kind !== 'edit' || !canSave) return
    setSaving(true)
    setSaveError(false)
    try {
      const frame =
        mode.entryId === null
          ? await sendMemory<MemoryEntryResult>({ op: 'create', title, body })
          : await sendMemory<MemoryEntryResult>({ op: 'update', entry_id: mode.entryId, title, body })
      const saved = frame.entry
      setEntries((previous) => {
        const next =
          mode.entryId === null
            ? [...previous, saved]
            : previous.map((entry) => (entry.id === saved.id ? saved : entry))
        return sortMemoryEntries(next)
      })
      setDirty(false)
      showList()
    } catch {
      setSaveError(true)
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (mode.kind !== 'view') return
    setSaving(true)
    setSaveError(false)
    try {
      await sendMemory({ op: 'delete', entry_id: mode.entryId })
      setEntries((previous) => previous.filter((entry) => entry.id !== mode.entryId))
      showList()
    } catch {
      setSaveError(true)
    } finally {
      setSaving(false)
    }
  }

  const onKey = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      if (confirmDelete) {
        setConfirmDelete(false)
      } else if (mode.kind === 'edit') {
        cancelEdit()
      } else if (mode.kind === 'view') {
        showList()
      }
      return
    }
    if (mode.kind === 'edit' && event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      void save()
    }
  }

  const sourceLabel = (source: string): string => {
    if (source === 'agent') return t('panel.memory.source.agent')
    if (source === 'ui') return t('panel.memory.source.ui')
    return source
  }

  const deleteConfirm = confirmDelete && (
    <div className={css.confirmBar} data-memory-confirm>
      <span>{t('panel.memory.deleteConfirm')}</span>
      <button
        type="button"
        className={css.danger}
        data-memory-delete-yes
        disabled={saving}
        onClick={() => void remove()}
      >
        {t('panel.memory.deleteYes')}
      </button>
      <button
        type="button"
        className={css.ghost}
        disabled={saving}
        onClick={() => setConfirmDelete(false)}
      >
        {t('panel.memory.cancel')}
      </button>
    </div>
  )

  if (mode.kind === 'view' && viewed !== null) {
    return (
      <div className={css.root} data-memory-panel data-mode="view" onKeyDown={onKey}>
        <div className={css.header}>
          <button
            type="button"
            className={css.iconButton}
            aria-label={t('panel.memory.back')}
            title={t('panel.memory.back')}
            onClick={showList}
          >
            <ArrowLeftIcon size={14} />
          </button>
          <span className={css.headerTitle} title={viewed.title}>
            {viewed.title}
          </span>
          <button
            type="button"
            className={css.iconButton}
            aria-label={t('panel.memory.edit')}
            title={t('panel.memory.edit')}
            onClick={() => startEdit(viewed)}
          >
            <PencilIcon size={14} />
          </button>
          <button
            type="button"
            className={css.iconButton}
            data-memory-delete
            aria-label={t('panel.memory.delete')}
            title={t('panel.memory.delete')}
            disabled={saving}
            onClick={() => setConfirmDelete(true)}
          >
            <TrashIcon size={14} />
          </button>
        </div>
        {deleteConfirm}
        <div className={css.viewer}>
          <div className={css.meta}>
            <span className={css.id}>#{viewed.id}</span>
            {viewed.source !== '' && (
              <span className={css.source} data-source={viewed.source}>
                {sourceLabel(viewed.source)}
              </span>
            )}
            <span className={css.rowTime}>{displayTime(viewed.updated)}</span>
          </div>
          {viewed.body.trim() === '' ? (
            <p className={css.note}>{t('panel.memory.emptyBody')}</p>
          ) : (
            <div
              className={chatCss.markdown}
              data-memory-markdown
              dangerouslySetInnerHTML={{ __html: markdown }}
            />
          )}
        </div>
        {saveError && <p className={css.error}>{t('panel.memory.saveError')}</p>}
      </div>
    )
  }

  if (mode.kind === 'edit') {
    return (
      <div className={css.root} data-memory-panel data-mode="edit" onKeyDown={onKey}>
        <div className={css.header}>
          <button
            type="button"
            className={css.iconButton}
            aria-label={
              mode.entryId === null
                ? t('panel.memory.back')
                : t('panel.memory.backToView')
            }
            title={
              mode.entryId === null
                ? t('panel.memory.back')
                : t('panel.memory.backToView')
            }
            onClick={cancelEdit}
          >
            <ArrowLeftIcon size={14} />
          </button>
          <span className={css.headerTitle}>
            {mode.entryId === null ? t('panel.memory.new') : t('panel.memory.edit')}
          </span>
        </div>
        <div className={css.editor}>
          <input
            className={css.titleInput}
            data-memory-title
            value={title}
            placeholder={t('panel.memory.title')}
            aria-label={t('panel.memory.title')}
            disabled={saving}
            onChange={(event) => {
              setTitle(event.target.value)
              setDirty(true)
            }}
          />
          <textarea
            className={css.bodyInput}
            data-memory-body
            value={body}
            placeholder={t('panel.memory.body')}
            aria-label={t('panel.memory.body')}
            disabled={saving}
            onChange={(event) => {
              setBody(event.target.value)
              setDirty(true)
            }}
          />
        </div>
        {saveError && <p className={css.error}>{t('panel.memory.saveError')}</p>}
        <div className={css.footer}>
          <button type="button" className={css.ghost} disabled={saving} onClick={cancelEdit}>
            {t('panel.memory.cancel')}
          </button>
          <button
            type="button"
            className={css.primary}
            data-memory-save
            disabled={!canSave}
            onClick={() => void save()}
          >
            {saving ? t('panel.memory.saving') : t('panel.memory.save')}
          </button>
        </div>
      </div>
    )
  }

  const visible = filterMemoryEntries(entries, query)
  const shown = revealedCount(visible.length, revealPages, MEMORY_REVEAL_BATCH)
  const hidden = visible.length - shown

  return (
    <div className={css.root} data-memory-panel data-mode="list">
      <div className={css.toolbar}>
        <label className={css.search}>
          <SearchIcon size={13} />
          <input
            value={query}
            placeholder={t('panel.memory.search')}
            aria-label={t('panel.memory.search')}
            onChange={(event) => {
              setQuery(event.target.value)
              setRevealPages(1)
            }}
          />
        </label>
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.memory.refresh')}
          title={t('panel.memory.refresh')}
          onClick={() => void load()}
        >
          <RefreshIcon size={14} />
        </button>
        <button
          type="button"
          className={css.add}
          data-memory-add
          onClick={() => startEdit(null)}
        >
          <PlusIcon size={13} />
          <span>{t('panel.memory.add')}</span>
        </button>
      </div>
      <div className={css.count}>{t('panel.memory.count', { count: entries.length })}</div>
      <div className={css.list}>
        {loading && entries.length === 0 ? (
          <p className={css.note}>{t('panel.memory.loading')}</p>
        ) : error ? (
          <p className={css.note}>{t('panel.memory.error')}</p>
        ) : entries.length === 0 ? (
          <div className={css.empty}>
            <SparkIcon size={20} />
            <span>{t('panel.memory.empty')}</span>
            <span className={css.emptyHint}>{t('panel.memory.emptyHint')}</span>
          </div>
        ) : visible.length === 0 ? (
          <p className={css.note}>{t('panel.memory.noMatch')}</p>
        ) : (
          visible.slice(0, shown).map((entry) => {
            const preview = snippet(entry.body)
            return (
              <button
                key={entry.id}
                type="button"
                className={css.row}
                data-memory-id={entry.id}
                onClick={() => openEntry(entry)}
              >
                <span className={css.rowTop}>
                  <span className={css.rowTitle}>{entry.title}</span>
                  <span className={css.rowTime}>{displayTime(entry.updated)}</span>
                </span>
                <span className={css.rowMeta}>
                  <span className={css.id}>#{entry.id}</span>
                  {entry.source !== '' && (
                    <span className={css.source} data-source={entry.source}>
                      {sourceLabel(entry.source)}
                    </span>
                  )}
                </span>
                {preview !== '' && <span className={css.snippet}>{preview}</span>}
              </button>
            )
          })
        )}
        {hidden > 0 && (
          <button
            type="button"
            className={css.more}
            onClick={() => setRevealPages((value) => value + 1)}
          >
            {t('sidebar.more', { count: hidden })}
          </button>
        )}
      </div>
    </div>
  )
}
