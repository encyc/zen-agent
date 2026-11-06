/** Dismissible action notice: the one place user-initiated RPC failures and
 * export results surface. */

import { useNoticeStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { XIcon } from './icons'
import css from './NoticeBanner.module.css'

export function NoticeBanner() {
  const notice = useNoticeStore((state) => state.notice)
  const dismiss = useNoticeStore((state) => state.dismiss)
  const t = useT()
  if (notice === null) return null
  return (
    <div className={css.root} data-kind={notice.kind} role="alert">
      <span className={css.text}>{notice.text}</span>
      <button type="button" className={css.dismiss} aria-label={t('notice.dismiss')} onClick={dismiss}>
        <XIcon size={12} />
      </button>
    </div>
  )
}
