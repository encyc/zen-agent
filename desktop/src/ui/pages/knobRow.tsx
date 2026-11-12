/** One config knob: copy on the left, an editor on the right. Shared by the
 * Parameters page and the Zen/Swarm pages so every knob edits the same way. */

import { useEffect, useState } from 'react'
import { useT } from '../../i18n/locale'
import type { LimitKnob } from '../../rpc/types'
import css from './LimitSettings.module.css'

function formatDefault(knob: LimitKnob): string {
  if (knob.default === null) return '—'
  if (knob.kind === 'str') return String(knob.default)
  return knob.kind === 'float' ? String(knob.default) : knob.default.toLocaleString()
}

/** Parse one draft against its knob; null = keep (untouched), undefined = invalid. */
export function parseDraft(knob: LimitKnob, raw: string): number | string | null | undefined {
  const text = raw.trim()
  const original = knob.value === null ? '' : String(knob.value)
  if (text === original) return null // unchanged; nothing to send
  if (knob.kind === 'str') {
    return knob.choices.includes(text) ? text : undefined
  }
  if (text === '' && knob.nullable) return null // "uncapped" stays as-is
  const parsed = knob.kind === 'float' ? Number.parseFloat(text) : Number.parseInt(text, 10)
  if (Number.isNaN(parsed)) return undefined
  if (parsed < knob.min || parsed > knob.max) return undefined
  return parsed
}

export function KnobRow({
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
    <div
      className={css.knob}
      data-knob={knob.key}
      data-explicit={knob.explicit || undefined}
      data-invalid={invalid || undefined}
    >
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
            <span className={css.defaultNote}>
              {' · '}
              {t('settings.limitDefault', { value: formatDefault(knob) })}
            </span>
          )}
        </div>
      </div>
      <div className={css.control}>
        {editing ? (
          knob.kind === 'str' ? (
            <select
              className={css.select}
              value={draft}
              aria-label={knob.key}
              onChange={(event) => change(event.target.value)}
            >
              {knob.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
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
          )
        ) : (
          <code className={css.value}>
            {knob.value === null
              ? '—'
              : knob.kind === 'float'
                ? String(knob.value)
                : knob.kind === 'str'
                  ? String(knob.value)
                  : knob.value.toLocaleString()}
          </code>
        )}
      </div>
    </div>
  )
}
