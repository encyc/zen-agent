/** Left sidebar: brand row (window-drag region), session browser, footer.
 * Session rows carry the dsh pattern: hover-revealed row actions and a
 * current-session accent. */

import { useMemo, useState, type MouseEvent } from 'react'
import { useAppStore } from '../state/appStore'
import { useSessionStore, useSettingsStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { Tooltip } from './primitives'
import { ChatIcon, MoonIcon, MonitorIcon, PanelLeftIcon, PinIcon, PlusIcon, SunIcon, TrashIcon, ForkIcon } from './icons'
import { formatAge } from './format'
import css from './Sidebar.module.css'

export function Sidebar(props: { onCollapse(): void }) {
  const t = useT()
  const sessions = useSessionStore((state) => state.sessions)
  const currentPath = useSessionStore((state) => state.currentPath)
  const newSession = useAppStore((state) => state.newSession)
  const forkSession = useAppStore((state) => state.forkSession)
  const deleteSession = useAppStore((state) => state.deleteSession)
  const resumeSession = useAppStore((state) => state.resumeSession)
  const [query, setQuery] = useState('')

  const { pinned, rest } = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = q
      ? sessions.filter((session) => (session.title ?? '').toLowerCase().includes(q) || session.name.toLowerCase().includes(q))
      : sessions
    return {
      pinned: filtered.filter((session) => session.pinned),
      rest: filtered.filter((session) => !session.pinned),
    }
  }, [sessions, query])

  return (
    <div className={css.root}>
      <div className={css.brandRow} data-tauri-drag-region>
        <div className={css.brandMark} aria-hidden>
          <SparkMark />
        </div>
        <div className={css.brandText} data-tauri-drag-region>
          <span className={css.brandName} data-tauri-drag-region>{t('brand.name')}</span>
        </div>
        <Tooltip label="⌘B">
          <button
            type="button"
            className={css.iconButton}
            aria-label="toggle sidebar"
            onClick={props.onCollapse}
          >
            <PanelLeftIcon size={15} />
          </button>
        </Tooltip>
      </div>

      <div className={css.actions}>
        <button type="button" className={css.newButton} onClick={() => void newSession()}>
          <PlusIcon size={14} />
          <span>{t('sidebar.new')}</span>
        </button>
      </div>

      <div className={css.searchWrap}>
        <input
          className={css.search}
          type="text"
          placeholder={t('sidebar.search')}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          spellCheck={false}
        />
      </div>

      <div className={css.list} data-selectable={false}>
        {pinned.length > 0 && (
          <div className={css.groupLabel}>{t('sidebar.pinned')}</div>
        )}
        {pinned.map((session) => (
          <SessionRow
            key={session.path}
            path={session.path}
            title={session.title || session.name}
            messages={session.messages}
            updatedAt={session.updated_at}
            model={session.model}
            derivedFrom={session.derived_from}
            current={session.path === currentPath}
            pinned
            onOpen={() => void resumeSession(session.path)}
            onFork={() => void forkSession()}
            onDelete={() => void deleteSession(session.path)}
          />
        ))}
        {pinned.length > 0 && rest.length > 0 && (
          <div className={css.groupLabel}>{t('sidebar.sessions')}</div>
        )}
        {rest.map((session) => (
          <SessionRow
            key={session.path}
            path={session.path}
            title={session.title || session.name}
            messages={session.messages}
            updatedAt={session.updated_at}
            model={session.model}
            derivedFrom={session.derived_from}
            current={session.path === currentPath}
            pinned={false}
            onOpen={() => void resumeSession(session.path)}
            onFork={() => void forkSession()}
            onDelete={() => void deleteSession(session.path)}
          />
        ))}
        {pinned.length === 0 && rest.length === 0 && (
          <div className={css.empty}>
            <ChatIcon size={16} />
            <span>{t('sidebar.empty')}</span>
          </div>
        )}
      </div>

      <Footer />
    </div>
  )
}

function SessionRow(props: {
  path: string
  title: string
  messages: number
  updatedAt?: number
  model?: string
  derivedFrom?: string
  current: boolean
  pinned: boolean
  onOpen(): void
  onFork(): void
  onDelete(): void
}) {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const lineage = props.derivedFrom
    ? t('session.forkedFrom', { name: props.derivedFrom })
    : null
  const sub = [
    `${props.messages} ${t('session.messages')}`,
    props.model ?? '',
    formatAge(props.updatedAt, locale),
  ]
    .filter((part) => part !== '')
    .join(' · ')
  const confirmDelete = (event: MouseEvent) => {
    event.stopPropagation()
    if (window.confirm(`${t('session.delete')}: ${props.title}?`)) props.onDelete()
  }
  return (
    <div
      className={css.sessionRow}
      data-current={props.current || undefined}
      onClick={props.onOpen}
      role="button"
      tabIndex={0}
      title={lineage ?? props.title}
      onKeyDown={(event) => {
        if (event.key === 'Enter') props.onOpen()
      }}
    >
      <div className={css.sessionIcon}>
        <ChatIcon size={14} />
      </div>
      <div className={css.sessionMeta}>
        <span className={css.sessionTitle}>{props.title}</span>
        <span className={css.sessionSub}>{sub}</span>
      </div>
      {props.derivedFrom && (
        <span className={css.sessionLineage} title={lineage ?? undefined}>
          <ForkIcon size={10} />
        </span>
      )}
      {props.pinned && (
        <span className={css.sessionPin}><PinIcon size={11} /></span>
      )}
      <div className={css.sessionActions}>
        <button type="button" aria-label={t('session.fork')} onClick={(event) => { event.stopPropagation(); props.onFork() }}>
          <ForkIcon size={12} />
        </button>
        <button type="button" aria-label={t('session.delete')} onClick={confirmDelete}>
          <TrashIcon size={12} />
        </button>
      </div>
    </div>
  )
}

function Footer() {
  const t = useT()
  const theme = useSettingsStore((state) => state.theme)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const next = theme === 'light' ? 'dark' : theme === 'dark' ? 'auto' : 'light'
  const icon = theme === 'light' ? <SunIcon size={14} /> : theme === 'dark' ? <MoonIcon size={14} /> : <MonitorIcon size={14} />
  const label = theme === 'light' ? t('theme.light') : theme === 'dark' ? t('theme.dark') : t('theme.auto')
  return (
    <div className={css.footer}>
      <button
        type="button"
        className={css.themeButton}
        onClick={() => setTheme(next)}
        aria-label={`${t('menu.theme')}: ${label}`}
      >
        {icon}
        <span>{label}</span>
      </button>
    </div>
  )
}

function SparkMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="1" y="1" width="16" height="16" rx="4.5" fill="var(--dsw-alias-state-business-primary)" />
      <path
        d="M9 4.2 10.3 7.7 13.8 9l-3.5 1.3L9 13.8 7.7 10.3 4.2 9l3.5-1.3Z"
        fill="var(--dsw-alias-label-primary-foreground)"
      />
    </svg>
  )
}
