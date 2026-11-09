/** Limits & budgets: every tunable the backend exposes through `[limits]` and
 * `[task]`, in one registry-driven page. The server owns the values, the
 * ranges and the write-through; this component only renders what it gets.
 *
 * One edit surface: the page is read-only until 「修改」, which dims the copy
 * and lights the editors; the same button becomes 「保存」 and submits every
 * changed knob in one batch. One bad value rejects the whole batch (the file
 * is never partially written), and the row stays marked for fixing. */

import { useEffect, useMemo, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useLocale, useT } from '../../i18n/locale'
import type { LimitKnob } from '../../rpc/types'
import { copyText } from '../clipboard'
import { CopyIcon } from '../icons'
import css from './LimitSettings.module.css'

function messageOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function formatDefault(knob: LimitKnob): string {
  if (knob.default === null) return '—'
  return knob.kind === 'float' ? String(knob.default) : knob.default.toLocaleString()
}

function pathLabel(path: string): string {
  const home = path.match(/^\/(?:Users|home)\/[^/]+(\/.*)$/)
  return home ? `~${home[1]}` : path
}

/** Parse one draft against its knob; null = keep (untouched), undefined = invalid. */
function parseDraft(knob: LimitKnob, raw: string): number | null | undefined {
  const text = raw.trim()
  const original = knob.value === null ? '' : String(knob.value)
  if (text === original) return null // unchanged; nothing to send
  if (text === '' && knob.nullable) return null // "uncapped" stays as-is
  const parsed = knob.kind === 'float' ? Number.parseFloat(text) : Number.parseInt(text, 10)
  if (Number.isNaN(parsed)) return undefined
  if (parsed < knob.min || parsed > knob.max) return undefined
  return parsed
}

/** One knob row. Editing is gated by the parent's `editing` flag; drafts live
 * here (so each row keeps its own undo), but only the parent's save sends. */
function KnobRow({
  knob,
  editing,
  invalid,
  onDraft,
  onSave,
}: {
  knob: LimitKnob
  editing: boolean
  invalid: boolean
  onDraft: (key: string, raw: string) => void
  onSave: () => void
}) {
  const t = useT()
  const [draft, setDraft] = useState(String(knob.value ?? ''))

  useEffect(() => {
    setDraft(knob.value === null ? '' : String(knob.value))
  }, [knob.value])

  function change(raw: string) {
    setDraft(raw)
    onDraft(knob.key, raw)
  }

  return (
    <div className={css.knob} data-knob={knob.key} data-explicit={knob.explicit || undefined} data-invalid={invalid || undefined}>
      <div className={css.copy}>
        <div className={css.nameRow}>
          <code className={css.name}>{knob.name}</code>
          {knob.apply === 'session' ? (
            <span className={css.chip}>{t('settings.limitNewSession')}</span>
          ) : (
            <span className={css.chip}>{t('settings.limitImmediate')}</span>
          )}
          {knob.override ? (
            <span className={css.chipOverride} title={knob.override}>
              {t('settings.limitOverride', { flags: knob.override })}
            </span>
          ) : null}
        </div>
        <div className={css.detail}>
          {t(`settings.limit.${knob.key.replace(/^limits\./, '')}`)}
          {knob.explicit ? null : (
            <span className={css.defaultNote}> · {t('settings.limitDefault', { value: formatDefault(knob) })}</span>
          )}
        </div>
      </div>
      <div className={css.control}>
        {editing ? (
          <input
            className={css.input}
            type="text"
            inputMode={knob.kind === 'float' ? 'decimal' : 'numeric'}
            value={draft}
            aria-label={knob.key}
            aria-invalid={invalid || undefined}
            min={knob.min}
            max={knob.max}
            onChange={(event) => change(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onSave()
            }}
          />
        ) : (
          <code className={css.value}>{knob.value === null ? '—' : knob.kind === 'float' ? String(knob.value) : knob.value.toLocaleString()}</code>
        )}
      </div>
    </div>
  )
}

export function LimitSettings() {
  const t = useT()
  const locale = useLocale()
  const fetchLimits = useAppStore((state) => state.fetchLimits)
  const [knobs, setKnobs] = useState<LimitKnob[]>([])
  const [path, setPath] = useState('')
  const [projectPath, setProjectPath] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const apply = (frame: Record<string, unknown>) => {
    if (frame.type === 'result' && frame.ok) {
      setKnobs((frame.knobs ?? []) as LimitKnob[])
      setPath(String(frame.path ?? ''))
      setProjectPath(String(frame.project_path ?? ''))
      setError(typeof frame.error === 'string' ? frame.error : '')
    } else {
      setError(messageOf(frame.error) || messageOf(frame.message) || t('settings.limitLoadFailed'))
    }
  }

  useEffect(() => {
    let alive = true
    setLoading(true)
    fetchLimits()
      .then((frame) => {
        if (alive) apply(frame)
      })
      .catch((cause) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
    // `locale` stands in for `t`, which is recreated per render.
  }, [fetchLimits, locale])

  const knobByKey = useMemo(() => new Map(knobs.map((knob) => [knob.key, knob])), [knobs])

  /** Which drafts would actually change something, and which of those are invalid. */
  const pending = useMemo(() => {
    const changes: { knob: LimitKnob; value: number | null }[] = []
    const bad: Set<string> = new Set()
    for (const [key, raw] of Object.entries(drafts)) {
      const knob = knobByKey.get(key)
      if (!knob) continue
      const parsed = parseDraft(knob, raw)
      if (parsed === undefined) {
        bad.add(key)
      } else if (parsed !== null) {
        changes.push({ knob, value: parsed })
      }
    }
    return { changes, bad }
  }, [drafts, knobByKey])

  function setDraft(key: string, raw: string) {
    setDrafts((current) => ({ ...current, [key]: raw }))
  }

  function toggleEdit() {
    if (editing) {
      void saveAll()
      return
    }
    setDrafts({})
    setEditing(true)
  }

  async function saveAll() {
    // Batch discipline: one bad value rejects the whole batch — the file is
    // never partially written. The rows stay in edit mode for fixing.
    if (pending.bad.size > 0 || pending.changes.length === 0 || saving) return
    setSaving(true)
    try {
      let frame: Record<string, unknown> | null = null
      for (const { knob, value } of pending.changes) {
        frame = await useAppStore.getState().setLimit(knob.key, value)
      }
      if (frame) apply(frame)
      setDrafts({})
      setEditing(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  function cancelEdit() {
    setDrafts({})
    setEditing(false)
    setError('')
  }

  const groups = useMemo(() => {
    const bySection = new Map<string, LimitKnob[]>()
    for (const knob of knobs) {
      const rows = bySection.get(knob.section) ?? []
      rows.push(knob)
      bySection.set(knob.section, rows)
    }
    return [...bySection.entries()]
  }, [knobs])

  const sectionTitle = (section: string) => t(`settings.limitGroup.${section}`)
  const sectionCaption = (section: string) => t(`settings.limitGroupCaption.${section}`)

  const copyPath = async () => {
    if (await copyText(path)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  // The button names the batch the user *attempted* (changed drafts), so a
  // blocked save still says what it is refusing to send.
  const changedCount = useMemo(() => {
    let attempted = 0
    for (const [key, raw] of Object.entries(drafts)) {
      const knob = knobByKey.get(key)
      if (!knob) continue
      if (parseDraft(knob, raw) !== null) attempted += 1
    }
    return attempted
  }, [drafts, knobByKey])
  const blocked = editing && pending.bad.size > 0

  return (
    <div className={css.stack} data-editing={editing || undefined}>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div className={css.headCopy}>
            <h2 className={css.cardHeading}>{t('settings.limitFile')}</h2>
            <p className={css.cardCaption}>{t('settings.limitFileCaption')}</p>
          </div>
          <div className={css.headActions}>
            <button
              type="button"
              className={css.editButton}
              disabled={loading || saving || (editing && blocked)}
              onClick={() => void toggleEdit()}
            >
              {saving
                ? t('settings.limitSaving')
                : editing
                  ? changedCount > 0
                    ? t('settings.limitSaveCount', { count: changedCount })
                    : t('settings.limitSave')
                  : t('settings.limitEdit')}
            </button>
            {editing ? (
              <button type="button" className={css.cancelButton} disabled={saving} onClick={cancelEdit}>
                {t('settings.limitCancel')}
              </button>
            ) : null}
            {editing && blocked ? <span className={css.blockedNote}>{t('settings.limitBlocked')}</span> : null}
          </div>
        </div>
        <div className={css.fileRow}>
          <code className={css.filePath}>{pathLabel(path)}</code>
          <button
            type="button"
            className={css.copyButton}
            onClick={() => void copyPath()}
            aria-label={t('settings.limitCopyPath')}
          >
            <CopyIcon size={12} />
            <span>{copied ? t('settings.limitCopied') : t('settings.limitCopyPath')}</span>
          </button>
        </div>
        {projectPath ? (
          <div className={css.fileNote}>{t('settings.limitProjectNote', { path: pathLabel(projectPath) })}</div>
        ) : null}
      </section>

      {loading ? <div className={css.hint}>{t('settings.limitLoading')}</div> : null}
      {error ? <div className={css.error}>{error}</div> : null}

      {groups.map(([section, rows]) => (
        <section className={css.cardBlock} key={section} data-group={section}>
          <div className={css.cardHeadline}>
            <div className={css.headCopy}>
              <h2 className={css.cardHeading}>{sectionTitle(section)}</h2>
              <p className={css.cardCaption}>{sectionCaption(section)}</p>
            </div>
          </div>
          <div className={css.panel}>
            {rows.map((knob) => (
              <KnobRow
                key={knob.key}
                knob={knob}
                editing={editing}
                invalid={editing && pending.bad.has(knob.key)}
                onDraft={setDraft}
                onSave={() => void saveAll()}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
