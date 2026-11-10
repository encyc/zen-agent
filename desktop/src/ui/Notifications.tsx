/** Local notification feed: every notice the app shows also lands here, so the
 * footer bell can reopen action errors after the banner is gone. A waiting
 * release rides the same bell: the offer needs a click, not a strip above the
 * composer. */

import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../state/appStore'
import {
  useApprovalStore,
  useNotificationStore,
  useSettingsStore,
} from '../state/stores'
import { updateNotice } from '../state/update'
import { useUpdateStore } from '../state/updateStore'
import { useT } from '../i18n/locale'
import { formatAge } from './format'
import { BellIcon, XIcon } from './icons'
import css from './Notifications.module.css'

const RELEASES = 'https://github.com/encyc/zen-agent/releases/latest'

export function Notifications() {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const updateChecks = useSettingsStore((state) => state.updateChecks)
  const items = useNotificationStore((state) => state.items)
  const unread = useNotificationStore((state) => state.unread)
  const markRead = useNotificationStore((state) => state.markRead)
  const clear = useNotificationStore((state) => state.clear)
  const cache = useUpdateStore((state) => state.cache)
  const phase = useUpdateStore((state) => state.phase)
  const failure = useUpdateStore((state) => state.message)
  const selfUpdatable = useUpdateStore((state) => state.selfUpdatable)
  const install = useUpdateStore((state) => state.install)
  const dismissUpdate = useUpdateStore((state) => state.dismiss)
  const busy = useAppStore((state) => state.busy)
  const pendingApprovals = useApprovalStore((state) => state.queue.length)
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (anchorRef.current?.contains(event.target as Node)) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      event.stopPropagation()
      event.stopImmediatePropagation()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open])

  const toggle = () => {
    if (!open) markRead()
    setOpen(!open)
  }

  const notice = updateNotice({
    cache,
    current: __APP_VERSION__,
    enabled: __UPDATE_ENABLED__ && updateChecks,
    busy,
    pendingApprovals,
    phase,
    selfUpdatable,
  })
  const hasNotice = notice.kind !== 'hidden'
  const version = 'version' in notice ? notice.version : ''
  const noticeText =
    notice.kind === 'available'
      ? t('update.available', { version })
      : notice.kind === 'blocked'
        ? t('update.blocked', { version })
        : notice.kind === 'installing'
          ? t('update.installing', { version })
          : notice.kind === 'manual'
            ? t('update.manual', { version })
            : t('update.failed')
  const dotTone = unread > 0 ? 'error' : 'update'
  const showDot = unread > 0 || (hasNotice && !open)

  return (
    <div className={css.anchor} ref={anchorRef}>
      <button
        type="button"
        className={css.button}
        aria-label={t('notifications.title')}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={toggle}
      >
        <BellIcon size={14} />
        {showDot && <span className={css.dot} data-tone={dotTone} data-testid="notification-dot" />}
      </button>
      {open && (
        <div className={css.popover} role="dialog" aria-label={t('notifications.title')}>
          <div className={css.header}>
            <span>{t('notifications.title')}</span>
            {items.length > 0 && (
              <button type="button" className={css.clear} onClick={clear}>
                {t('notifications.clear')}
              </button>
            )}
          </div>
          {hasNotice && (
            <div
              className={css.update}
              data-kind={notice.kind === 'failed' ? 'error' : 'info'}
              data-update-notice={notice.kind}
              role="status"
            >
              <div className={css.updateText}>
                <span>{noticeText}</span>
                {notice.kind === 'failed' && failure !== null && (
                  <span className={css.updateDetail}>{failure}</span>
                )}
              </div>
              <div className={css.updateActions}>
                {notice.kind === 'available' && (
                  <button
                    type="button"
                    className={css.updateAction}
                    onClick={() => void install({ busy, pendingApprovals })}
                  >
                    {t('update.install')}
                  </button>
                )}
                {(notice.kind === 'manual' || notice.kind === 'failed') && (
                  <a
                    className={css.updateAction}
                    href={RELEASES}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {t('update.download')}
                  </a>
                )}
                {notice.kind !== 'installing' && (
                  <button
                    type="button"
                    className={css.updateDismiss}
                    aria-label={t('notice.dismiss')}
                    onClick={dismissUpdate}
                  >
                    <XIcon size={12} />
                  </button>
                )}
              </div>
            </div>
          )}
          {items.length === 0 ? (
            !hasNotice && <p className={css.empty}>{t('notifications.empty')}</p>
          ) : (
            <ul className={css.list}>
              {items.map((item) => (
                <li key={item.id} className={css.item} data-tone={item.tone}>
                  <span className={css.text}>{item.text}</span>
                  <span className={css.time}>
                    {formatAge(Math.floor(item.at / 1000), locale)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
