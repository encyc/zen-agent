/** A self-contained group of config knobs for one surface (Zen fleet, Swarm
 * rooms). Reads the same `limits` snapshot as the Parameters page and writes
 * through the same endpoint; only the rows it owns are rendered here. */

import { useEffect, useMemo, useState } from 'react'
import { useLocale, useT } from '../../i18n/locale'
import type { ErrorFrame, LimitKnob, LimitsResult } from '../../rpc/types'
import { frameErrorNotice } from '../../rpc/frames'
import { useAppStore } from '../../state/appStore'
import { KnobRow, parseDraft } from './knobRow'
import css from './LimitSettings.module.css'

function messageOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function KnobPanel(props: {
  table: string
  /** Knobs owned by another table but presented here (e.g. task.timeout_s). */
  extraKeys?: string[]
  title: string
  caption: string
}) {
  const t = useT()
  const locale = useLocale()
  const fetchLimits = useAppStore((state) => state.fetchLimits)
  const [knobs, setKnobs] = useState<LimitKnob[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const extra = props.extraKeys ?? []
  const owns = (knob: LimitKnob) =>
    knob.table === props.table || extra.includes(knob.key)

  const apply = (frame: LimitsResult | ErrorFrame) => {
    if (frame.type === 'error') {
      setError(frameErrorNotice(frame, locale) ?? t('settings.limitLoadFailed'))
      return
    }
    if (!frame.ok) {
      setError(messageOf(frame.error) || t('settings.limitLoadFailed'))
      return
    }
    setKnobs(frame.knobs.filter(owns))
    setError('')
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

  const pending = useMemo(() => {
    const changes: { knob: LimitKnob; value: number | string | null }[] = []
    const bad: Set<string> = new Set()
    for (const [key, raw] of Object.entries(drafts)) {
      const knob = knobByKey.get(key)
      if (!knob) continue
      const parsed = parseDraft(knob, raw)
      if (parsed === undefined) bad.add(key)
      else if (parsed !== null) changes.push({ knob, value: parsed })
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
    if (pending.bad.size > 0 || pending.changes.length === 0 || saving) return
    setSaving(true)
    try {
      let frame: LimitsResult | ErrorFrame | null = null
      for (const { knob, value } of pending.changes) {
        frame = await useAppStore.getState().setLimit(knob.key, value)
      }
      if (frame !== null) apply(frame)
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

  const changedCount = useMemo(() => {
    let attempted = 0
    for (const [key, raw] of Object.entries(drafts)) {
      const knob = knobByKey.get(key)
      if (knob && parseDraft(knob, raw) !== null) attempted += 1
    }
    return attempted
  }, [drafts, knobByKey])
  const blocked = editing && pending.bad.size > 0
  const shown = knobs.filter(owns)

  return (
    <section
      className={css.cardBlock}
      data-editing={editing || undefined}
      data-knob-table={props.table}
    >
      <div className={css.cardHeadline}>
        <div className={css.headCopy}>
          <h2 className={css.cardHeading}>{props.title}</h2>
          <p className={css.cardCaption}>{props.caption}</p>
        </div>
        <div className={css.headActions}>
          <button
            type="button"
            className={css.editButton}
            disabled={loading || saving || shown.length === 0 || (editing && blocked)}
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
            <button
              type="button"
              className={css.cancelButton}
              disabled={saving}
              onClick={cancelEdit}
            >
              {t('settings.limitCancel')}
            </button>
          ) : null}
          {blocked ? <span className={css.blockedNote}>{t('settings.limitBlocked')}</span> : null}
        </div>
      </div>
      {loading ? <div className={css.hint}>{t('settings.limitLoading')}</div> : null}
      {error ? <div className={css.error}>{error}</div> : null}
      {shown.length > 0 ? (
        <div className={css.panel}>
          {shown.map((knob) => (
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
      ) : null}
    </section>
  )
}
