/** Dismissible action notice: the one place user-initiated RPC failures and
 * export results surface. */

import { useRef } from 'react'
import { useNoticeStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { XIcon } from './icons'
import { inertProps, usePresence, PRESENCE_LEAVE_MS } from './presence'
import css from './NoticeBanner.module.css'

export function NoticeBanner() {
  const notice = useNoticeStore((state) => state.notice)
  const dismiss = useNoticeStore((state) => state.dismiss)
  const t = useT()
  const presence = usePresence(notice !== null, PRESENCE_LEAVE_MS)
  // The store drops the notice before the exit runs, so the leaving frames are
  // painted from the last live one — otherwise the text blanks out mid-fade.
  const lastNotice = useRef(notice)
  if (notice !== null) lastNotice.current = notice
  const shown = notice ?? lastNotice.current
  if (!presence.mounted || shown === null) return null
  return (
    <div
      className={css.root}
      data-kind={shown.kind}
      data-state={presence.state}
      role="alert"
      {...inertProps(presence.state === 'closed')}
    >
      <span className={css.text}>{shown.text}</span>
      <button
        type="button"
        className={css.dismiss}
        aria-label={t('notice.dismiss')}
        disabled={presence.state === 'closed'}
        onClick={dismiss}
      >
        <XIcon size={12} />
      </button>
    </div>
  )
}
