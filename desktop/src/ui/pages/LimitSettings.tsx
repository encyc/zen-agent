/** Limits & budgets: every tunable the backend exposes through `[limits]` and
 * `[task]`, in one registry-driven page. The server owns the values, the
 * ranges and the write-through; this component only renders what it gets. */

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

/** One knob row: value editor + provenance chips + reset. Editing is local
 * until Enter/blur; the server validates and answers with the whole page,
 * which the parent applies (the file, not this form, is the authority). */
function KnobRow({
  knob,
  onWrite,
}: {
  knob: LimitKnob
  onWrite: (op: 'set' | 'reset', key: string, value: number | null) => Promise<void>
}) {
  const t = useT()
  const [draft, setDraft] = useState(String(knob.value ?? ''))
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setDraft(knob.value === null ? '' : String(knob.value))
  }, [knob.value])

  const dirty = draft !== (knob.value === null ? '' : String(knob.value))

  async function commit() {
    if (busy || !dirty) return
    setBusy(true)
    try {
      const raw = draft.trim()
      if (raw === '' && knob.nullable) {
        await onWrite('set', knob.key, null)
      } else {
        const parsed = knob.kind === 'float' ? Number.parseFloat(raw) : Number.parseInt(raw, 10)
        if (Number.isNaN(parsed)) return
        await onWrite('set', knob.key, parsed)
      }
    } finally {
      setBusy(false)
    }
  }

  async function reset() {
    if (busy) return
    setBusy(true)
    try {
      await onWrite('reset', knob.key, null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.knob} data-knob={knob.key} data-explicit={knob.explicit || undefined}>
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
          {knob.explicit ? null : <span className={css.defaultNote}> · {t('settings.limitDefault', { value: formatDefault(knob) })}</span>}
        </div>
      </div>
      <div className={css.control}>
        <input
          className={css.input}
          type="text"
          inputMode={knob.kind === 'float' ? 'decimal' : 'numeric'}
          value={draft}
          disabled={busy}
          aria-label={knob.key}
          min={knob.min}
          max={knob.max}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void commit()
          }}
          onBlur={() => void commit()}
        />
        {knob.explicit ? (
          <button
            type="button"
            className={css.reset}
            disabled={busy}
            onClick={() => void reset()}
          >
            {t('settings.limitReset')}
          </button>
        ) : null}
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

  async function write(op: 'set' | 'reset', key: string, value: number | null) {
    const frame =
      op === 'set'
        ? await useAppStore.getState().setLimit(key, value)
        : await useAppStore.getState().resetLimit(key)
    apply(frame)
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

  return (
    <div className={css.stack}>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <h2 className={css.cardHeading}>{t('settings.limitFile')}</h2>
          <p className={css.cardCaption}>{t('settings.limitFileCaption')}</p>
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
            <h2 className={css.cardHeading}>{sectionTitle(section)}</h2>
            <p className={css.cardCaption}>{sectionCaption(section)}</p>
          </div>
          <div className={css.panel}>
            {rows.map((knob) => (
              <KnobRow key={knob.key} knob={knob} onWrite={write} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
