/** Local notification feed: every notice the app shows also lands here, so the
 * footer bell can reopen action errors after the banner is gone. */

import { useEffect, useRef, useState } from 'react'
import { useNotificationStore, useSettingsStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { formatAge } from './format'
import { BellIcon } from './icons'
import css from './Notifications.module.css'

export function Notifications() {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const items = useNotificationStore((state) => state.items)
  const unread = useNotificationStore((state) => state.unread)
  const markRead = useNotificationStore((state) => state.markRead)
  const clear = useNotificationStore((state) => state.clear)
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
        {unread > 0 && <span className={css.dot} data-testid="notification-dot" />}
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
          {items.length === 0 ? (
            <p className={css.empty}>{t('notifications.empty')}</p>
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
