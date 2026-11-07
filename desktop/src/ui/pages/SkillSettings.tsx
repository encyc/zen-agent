/** Skill browser: what the agent can load, where it lives, and a switch that
 * persists into `prefs.disabled_skills` so the runtime stops offering it. */

import { useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useSkillsStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import type { SkillRow } from '../../rpc/types'
import { RefreshIcon } from '../icons'
import css from './SkillSettings.module.css'

function messageOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function SkillCard({ row }: { row: SkillRow }) {
  const t = useT()
  const readSkill = useAppStore((state) => state.readSkill)
  const toggleSkill = useAppStore((state) => state.toggleSkill)
  const [open, setOpen] = useState(false)
  const [body, setBody] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function expand() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    if (body !== null) return
    setLoading(true)
    setError('')
    try {
      const frame = await readSkill(row.name)
      if (frame.type === 'result' && frame.ok) {
        setBody(String(frame.body ?? ''))
        setTruncated(frame.truncated === true)
      } else {
        setError(messageOf(frame.message) || t('settings.skillReadFailed'))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }

  async function flip(enabled: boolean) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const frame = await toggleSkill(row.name, enabled)
      if (!(frame.type === 'result' && frame.ok)) {
        setError(messageOf(frame.message) || t('settings.skillToggleFailed'))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.skill} data-skill={row.name} data-enabled={row.enabled || undefined}>
      <div className={css.head}>
        <button type="button" className={css.main} aria-expanded={open} onClick={() => void expand()}>
          <div className={css.copy}>
            <div className={css.name}>
              <span>{row.name}</span>
              <span className={css.source}>
                {t(row.source === 'project' ? 'settings.skillProject' : 'settings.skillGlobal')}
              </span>
            </div>
            <div className={css.description}>{row.description}</div>
            <div className={css.path}>{row.path}</div>
          </div>
        </button>
        <div className={css.actions}>
          <button type="button" className={css.action} onClick={() => void expand()}>
            {open ? t('settings.skillHide') : t('settings.skillPreview')}
          </button>
          <button
            type="button"
            role="switch"
            aria-checked={row.enabled}
            aria-label={t('settings.skillEnable', { name: row.name })}
            className={css.switch}
            disabled={busy}
            onClick={() => void flip(!row.enabled)}
          >
            <span className={css.switchThumb} />
          </button>
        </div>
      </div>
      {open ? (
        <div className={css.preview}>
          {loading ? <div className={css.hint}>{t('settings.skillLoading')}</div> : null}
          {body !== null ? <pre className={css.body}>{body}</pre> : null}
          {truncated ? <div className={css.hint}>{t('settings.skillTruncated')}</div> : null}
        </div>
      ) : null}
      {error ? <div className={css.error}>{error}</div> : null}
    </div>
  )
}

export function SkillSettings() {
  const t = useT()
  const skills = useSkillsStore((state) => state.skills)
  const refreshSkills = useAppStore((state) => state.refreshSkills)
  const rescanSkills = useAppStore((state) => state.rescanSkills)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void refreshSkills()
  }, [refreshSkills])

  const rescan = async () => {
    if (busy) return
    setBusy(true)
    try {
      await rescanSkills()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.stack}>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <h2 className={css.cardHeading}>{t('settings.skillsList')}</h2>
          <p className={css.cardCaption}>{t('settings.skillsListCaption')}</p>
          <button type="button" className={css.rescan} disabled={busy} onClick={() => void rescan()}>
            <RefreshIcon size={12} />
            <span>{busy ? t('settings.skillRescanning') : t('settings.skillRescan')}</span>
          </button>
        </div>
        <div className={css.panel}>
          {skills.length === 0 ? (
            <div className={css.empty}>{t('settings.skillsEmpty')}</div>
          ) : (
            skills.map((row) => <SkillCard key={row.name} row={row} />)
          )}
        </div>
      </section>
    </div>
  )
}
