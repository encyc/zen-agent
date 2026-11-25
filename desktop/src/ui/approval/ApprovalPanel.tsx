/** Approval panel — dsh ApprovalPanel: sits above the composer, which stays
 * mounted and usable. Buttons Allow once / Always / Reject; Enter allows, Esc
 * rejects; risky requests surface target + reason. */

import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useApprovalStore, useSessionStore } from '../../state/stores'
import { pendingForProject } from '../../state/pendingProject'
import { useT } from '../../i18n/locale'
import { StateDot } from '../primitives'
import { usePresence, PRESENCE_LEAVE_MS } from '../presence'
import css from './ApprovalPanel.module.css'

export function ApprovalPanel() {
  const queue = useApprovalStore((state) => state.queue)
  const project = useAppStore((state) => state.project)
  const session = useSessionStore((state) => state.currentPath)
  const approve = useAppStore((state) => state.approve)
  const t = useT()
  const approval = pendingForProject(queue, project, session)[0]
  const answered = useRef(false)
  const [busy, setBusy] = useState(false)
  const presence = usePresence(approval !== undefined, PRESENCE_LEAVE_MS)
  // The queue drops the request the moment it is answered; the exit frame is
  // painted from the last live one so the card does not blank out mid-fade.
  const lastApproval = useRef(approval)
  if (approval !== undefined) lastApproval.current = approval
  const shown = approval ?? lastApproval.current

  useEffect(() => {
    // Reset only when a NEW request arrives. The queue emptying means the
    // answer was consumed, and the exit frame has to keep `busy` so the buttons
    // stay disabled while the card fades out.
    if (approval === undefined) return
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

  if (!presence.mounted || shown === undefined) return null

  const answer = (decision: 'allow' | 'always' | 'deny') => {
    if (answered.current || busy) return
    answered.current = true
    setBusy(true)
    // Rejecting one request must not abort the run: the tool result comes
    // back as an error and the model reacts (same as the TUI's [n]).
    void approve(shown.requestId, decision)
  }

  let argsPreview = shown.args
  try {
    const parsed: unknown = JSON.parse(shown.args)
    if (parsed !== null && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>
      const first = record.command ?? record.path ?? record.query ?? record.pattern ?? record.url
      if (typeof first === 'string') argsPreview = first
    }
  } catch {
    // keep raw args
  }

  return (
    <div className={css.root} data-approval data-state={presence.state}>
      <div className={css.card}>
        <div className={css.strip}>
          <StateDot state="warning" />
          <span>{t('approval.waiting')}</span>
          <span className={css.hint}>{t('approval.hint')}</span>
        </div>
        <div className={css.body} data-selectable>
          <div className={css.headline}>
            {shown.reason ??
              t('approval.escalation', { toolName: shown.toolName })}
          </div>
          <div className={css.command}>
            <span className={css.toolName}>{shown.toolName}</span>
            <span className={css.toolArgs}>{argsPreview}</span>
          </div>
          {shown.argsTruncated && (
            <div className={css.risk}>{t('approval.truncated')}</div>
          )}
          {shown.risk !== null && shown.risk !== '' && (
            <div className={css.risk}>{shown.risk}{shown.target !== null ? ` · ${shown.target}` : ''}</div>
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
          {shown.target !== null && shown.target !== '' && (
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
