/** Approval panel — dsh ApprovalPanel: sits above the composer, which stays
 * mounted and usable. Buttons Allow once / Always / Reject; Enter allows, Esc
 * rejects; risky requests surface target + reason. */

import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useApprovalStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { StateDot } from '../primitives'
import css from './ApprovalPanel.module.css'

export function ApprovalPanel() {
  const queue = useApprovalStore((state) => state.queue)
  const approve = useAppStore((state) => state.approve)
  const t = useT()
  const approval = queue[0]
  const answered = useRef(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    answered.current = false
    setBusy(false)
  }, [approval?.requestId])

  useEffect(() => {
    if (approval === undefined) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (answered.current) return
      const target = event.target as Element | null
      if (target !== null && target.closest('input, textarea, select, [contenteditable]') !== null) return
      if (event.key === 'Enter') {
        event.preventDefault()
        answer('allow')
      } else if (event.key === 'Escape') {
        event.preventDefault()
        answer('deny')
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval?.requestId])

  if (approval === undefined) return null

  const answer = (decision: 'allow' | 'always' | 'deny') => {
    if (answered.current || busy) return
    answered.current = true
    setBusy(true)
    // Rejecting one request must not abort the run: the tool result comes
    // back as an error and the model reacts (same as the TUI's [n]).
    void approve(approval.requestId, decision)
  }

  let argsPreview = approval.args
  try {
    const parsed: unknown = JSON.parse(approval.args)
    if (parsed !== null && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>
      const first = record.command ?? record.path ?? record.query ?? record.pattern ?? record.url
      if (typeof first === 'string') argsPreview = first
    }
  } catch {
    // keep raw args
  }

  return (
    <div className={css.root} data-approval>
      <div className={css.card}>
        <div className={css.strip}>
          <StateDot state="warning" />
          <span>{t('approval.waiting')}</span>
          <span className={css.hint}>{t('approval.hint')}</span>
        </div>
        <div className={css.body} data-selectable>
          <div className={css.headline}>
            {approval.reason ??
              t('approval.escalation', { toolName: approval.toolName })}
          </div>
          <div className={css.command}>
            <span className={css.toolName}>{approval.toolName}</span>
            <span className={css.toolArgs}>{argsPreview}</span>
          </div>
          {approval.risk !== null && approval.risk !== '' && (
            <div className={css.risk}>{approval.risk}{approval.target !== null ? ` · ${approval.target}` : ''}</div>
          )}
        </div>
        <div className={css.actionRow}>
          <button
            type="button"
            className={css.reject}
            disabled={busy}
            onClick={() => answer('deny')}
          >
            {t('approval.reject')}
          </button>
          {approval.target !== null && approval.target !== '' && (
            <button
              type="button"
              className={css.always}
              disabled={busy}
              onClick={() => answer('always')}
            >
              {t('approval.always')}
            </button>
          )}
          <button
            type="button"
            className={css.allow}
            disabled={busy}
            onClick={() => answer('allow')}
          >
            {t('approval.allowOnce')}
          </button>
        </div>
      </div>
    </div>
  )
}
