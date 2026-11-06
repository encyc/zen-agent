/** Left sidebar: brand row (window-drag region), scope switch, session browser
 * and footer actions. Session rows carry the dsh pattern: hover-revealed menu
 * trigger, inline rename and a two-step delete. */

import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useAppStore } from '../state/appStore'
import { canForkSession, isCrossProject } from '../state/sessionSelectors'
import {
  currentPending,
  useNoticeStore,
  useSessionStore,
  useSettingsStore,
  useStatusStore,
} from '../state/stores'
import { sessionGroups, type SessionScope } from '../state/sessionGroups'
import { armDelete, deleteArmed } from '../state/armedDelete'
import { useT } from '../i18n/locale'
import { StateDot, Tooltip } from './primitives'
import {
  ChatIcon,
  FileIcon,
  FolderIcon,
  ForkIcon,
  MoonIcon,
  MonitorIcon,
  MoreIcon,
  PanelLeftIcon,
  PinIcon,
  PlusIcon,
  SunIcon,
} from './icons'
import { formatAge } from './format'
import { pickSessionFileToOpen } from './sessions/fileDialog'
import css from './Sidebar.module.css'

interface RowMenu {
  path: string
  left: number
  top: number
}

export function Sidebar(props: { onCollapse(): void }) {
  const t = useT()
  const sessions = useSessionStore((state) => state.sessions)
  const currentPath = useSessionStore((state) => state.currentPath)
  const scope = useSessionStore((state) => state.scope)
  const setScope = useSessionStore((state) => state.setScope)
  const refreshSessions = useAppStore((state) => state.refreshSessions)
  const renameSession = useAppStore((state) => state.renameSession)
  const togglePin = useAppStore((state) => state.togglePin)
  const deleteSession = useAppStore((state) => state.deleteSession)
  const forkSession = useAppStore((state) => state.forkSession)
  const resumeSession = useAppStore((state) => state.resumeSession)
  const busy = useAppStore((state) => state.busy)
  const pending = useStatusStore((state) => currentPending(state.status).length)
  const [query, setQuery] = useState('')
  const [menu, setMenu] = useState<RowMenu | null>(null)
  const [armed, setArmed] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  const { pinned, groups } = useMemo(
    () => sessionGroups(sessions, scope, query),
    [sessions, scope, query],
  )
  const current = sessions.find((session) => session.path === currentPath) ?? null
  const currentProject = current?.project ?? null
  const menuSession = menu === null ? null : sessions.find((session) => session.path === menu.path) ?? null

  const switchScope = (next: SessionScope) => {
    if (next === scope) return
    setArmed(null)
    setEditing(null)
    setMenu(null)
    setScope(next)
    void refreshSessions(next)
  }

  const openMenu = (event: MouseEvent<HTMLButtonElement>, path: string) => {
    event.stopPropagation()
    if (menu?.path === path) {
      closeMenu()
      return
    }
    triggerRef.current = event.currentTarget
    const rect = event.currentTarget.getBoundingClientRect()
    // Open leftward: a right-aligned panel would cover the next rows' triggers.
    const left = Math.max(8, rect.left - 176 - 4)
    setMenu({ path, left, top: rect.bottom + 4 })
  }

  const closeMenu = (restoreFocus = false) => {
    setMenu(null)
    setArmed(null)
    if (restoreFocus) triggerRef.current?.focus()
  }

  const commitRename = (path: string, title: string) => {
    setEditing(null)
    const previous = sessions.find((session) => session.path === path)
    const next = title.trim()
    if (next !== '' && next !== (previous?.title || previous?.name || '')) void renameSession(next)
  }

  const deleteRow = (path: string) => {
    if (deleteArmed(armed, path)) {
      setArmed(null)
      setMenu(null)
      void deleteSession(path)
      return
    }
    setArmed(armDelete(armed, path))
  }

  useEffect(() => {
    if (menu === null) return
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (menuRef.current?.contains(target)) return
      if (
        target instanceof Element &&
        target.closest('[data-session-path]')?.getAttribute('data-session-path') === menu.path
      ) {
        return
      }
      closeMenu()
    }
    // Capture phase: App's document listener would otherwise cancel the run
    // before this menu sees the Escape.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      closeMenu(true)
      event.stopPropagation()
      event.stopImmediatePropagation()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [menu])

  const renderRow = (session: (typeof sessions)[number]) => (
    <SessionRow
      key={session.path}
      path={session.path}
      title={session.title || session.name}
      messages={session.messages}
      updatedAt={session.updated_at}
      model={session.model}
      project={session.project}
      derivedFrom={session.derived_from}
      scope={scope}
      current={session.path === currentPath}
      pinned={session.pinned === true}
      crossProject={isCrossProject(session.path, session.project, currentPath, currentProject)}
      busy={busy}
      pending={session.path === currentPath ? pending : 0}
      editing={editing === session.path}
      menuOpen={menu?.path === session.path}
      onOpen={() => void resumeSession(session.path)}
      onMenu={(event) => openMenu(event, session.path)}
      onRename={(title) => commitRename(session.path, title)}
      onCancelRename={() => setEditing(null)}
    />
  )

  return (
    <div className={css.root} data-shell-sidebar>
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
        <button
          type="button"
          className={css.newButton}
          onClick={() => void useAppStore.getState().newSession()}
        >
          <PlusIcon size={14} />
          <span>{t('sidebar.new')}</span>
        </button>
      </div>

      <div className={css.scopeRow} role="group">
        <button
          type="button"
          className={css.scopeButton}
          aria-pressed={scope === 'project'}
          onClick={() => switchScope('project')}
        >
          {t('sidebar.scope.project')}
        </button>
        <button
          type="button"
          className={css.scopeButton}
          aria-pressed={scope === 'all'}
          onClick={() => switchScope('all')}
        >
          {t('sidebar.scope.all')}
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
        {pinned.length > 0 && <div className={css.groupLabel}>{t('sidebar.pinned')}</div>}
        {pinned.map(renderRow)}
        {groups.map((group) => (
          <Section key={group.project} project={group.project} scope={scope}>
            {group.sessions.map(renderRow)}
          </Section>
        ))}
        {pinned.length === 0 && groups.length === 0 && (
          <div className={css.empty}>
            <ChatIcon size={16} />
            <span>{t('sidebar.empty')}</span>
          </div>
        )}
      </div>

      <Footer />

      {menu !== null && menuSession !== null &&
        createPortal(
          <div
            ref={menuRef}
            className={css.menu}
            role="menu"
            style={{ left: menu.left, top: menu.top }}
            onKeyDown={(event) => {
              if (event.key === 'Tab') {
                event.preventDefault()
                closeMenu(true)
                return
              }
              if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
              event.preventDefault()
              const items = [
                ...(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []),
              ]
              if (items.length === 0) return
              const from = items.indexOf(document.activeElement as HTMLButtonElement)
              const next =
                (from + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
              items[next]?.focus()
            }}
          >
            {menuSession.path === currentPath && (
              <>
                <button
                  type="button"
                  role="menuitem"
                  className={css.menuItem}
                  onClick={() => {
                    setEditing(menuSession.path)
                    setMenu(null)
                  }}
                >
                  {t('session.rename')}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={css.menuItem}
                  onClick={() => {
                    setMenu(null)
                    void togglePin(menuSession.pinned === true)
                  }}
                >
                  {menuSession.pinned ? t('session.unpin') : t('session.pin')}
                </button>
                {canForkSession(currentPath, menuSession.path) && (
                  <button
                    type="button"
                    role="menuitem"
                    className={css.menuItem}
                    onClick={() => {
                      setMenu(null)
                      void forkSession()
                    }}
                  >
                    {t('session.fork')}
                  </button>
                )}
              </>
            )}
            <button
              type="button"
              role="menuitem"
              className={css.menuItemDanger}
              onClick={() => deleteRow(menuSession.path)}
            >
              {deleteArmed(armed, menuSession.path)
                ? t('session.deleteArmed')
                : t('session.delete')}
            </button>
          </div>,
          document.body,
        )}
    </div>
  )
}

function Section(props: { project: string; scope: SessionScope; children: ReactNode }) {
  const t = useT()
  return (
    <>
      {props.scope === 'all' && (
        <div className={css.groupLabel} data-project-group={props.project}>
          {props.project || t('sidebar.sessions')}
        </div>
      )}
      {props.children}
    </>
  )
}

function SessionRow(props: {
  path: string
  title: string
  messages: number
  updatedAt?: number
  model?: string
  project?: string
  derivedFrom?: string
  scope: SessionScope
  current: boolean
  pinned: boolean
  crossProject: boolean
  busy: boolean
  pending: number
  editing: boolean
  menuOpen: boolean
  onOpen(): void
  onMenu(event: MouseEvent<HTMLButtonElement>): void
  onRename(title: string): void
  onCancelRename(): void
}) {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const lineage = props.derivedFrom
    ? t('session.forkedFrom', { name: props.derivedFrom })
    : null
  const sub = [
    `${props.messages} ${t('session.messages')}`,
    props.scope === 'all' ? props.project ?? '' : '',
    props.model ?? '',
    formatAge(props.updatedAt, locale),
  ]
    .filter((part) => part !== '')
    .join(' · ')
  return (
    <div
      className={css.sessionRow}
      data-session-path={props.path}
      data-current={props.current || undefined}
      data-cross={props.crossProject || undefined}
      aria-disabled={props.crossProject || undefined}
      onClick={props.crossProject ? undefined : props.onOpen}
      role="button"
      tabIndex={0}
      title={props.crossProject ? t('sidebar.crossProject') : lineage ?? props.title}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && !props.crossProject) props.onOpen()
      }}
    >
      <div className={css.sessionIcon}>
        <ChatIcon size={14} />
      </div>
      <div className={css.sessionMeta}>
        {props.editing ? (
          <RenameInput
            value={props.title}
            label={t('topbar.title')}
            onCommit={props.onRename}
            onCancel={props.onCancelRename}
          />
        ) : (
          <span className={css.sessionTitle}>{props.title}</span>
        )}
        <span className={css.sessionSub}>{sub}</span>
      </div>
      {props.current && props.busy && (
        <span className={css.sessionCue} title={t('session.running')}>
          <StateDot state="running" size={6} />
        </span>
      )}
      {props.current && props.pending > 0 && (
        <span className={css.sessionPending}>{t('session.pending', { count: props.pending })}</span>
      )}
      {props.derivedFrom && (
        <span className={css.sessionLineage} title={lineage ?? undefined}>
          <ForkIcon size={10} />
        </span>
      )}
      {props.pinned && (
        <span className={css.sessionPin}><PinIcon size={11} /></span>
      )}
      <button
        type="button"
        className={css.rowMenuTrigger}
        data-action="menu"
        aria-label={t('sidebar.actions')}
        aria-haspopup="menu"
        aria-expanded={props.menuOpen}
        onClick={props.onMenu}
      >
        <MoreIcon size={13} />
      </button>
    </div>
  )
}

function RenameInput(props: {
  value: string
  label: string
  onCommit(title: string): void
  onCancel(): void
}) {
  const [draft, setDraft] = useState(props.value)
  const settled = useRef(false)
  const commit = () => {
    if (settled.current) return
    settled.current = true
    props.onCommit(draft)
  }
  const cancel = () => {
    if (settled.current) return
    settled.current = true
    props.onCancel()
  }
  return (
    <input
      className={css.renameInput}
      aria-label={props.label}
      value={draft}
      autoFocus
      onClick={(event) => event.stopPropagation()}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={cancel}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key === 'Enter') {
          event.preventDefault()
          commit()
        } else if (event.key === 'Escape') {
          event.preventDefault()
          cancel()
        }
      }}
    />
  )
}

function Footer() {
  const t = useT()
  const theme = useSettingsStore((state) => state.theme)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const importSession = useAppStore((state) => state.importSession)
  const exportSession = useAppStore((state) => state.exportSession)
  const [exportOpen, setExportOpen] = useState(false)
  const exportRef = useRef<HTMLDivElement | null>(null)
  const next = theme === 'light' ? 'dark' : theme === 'dark' ? 'auto' : 'light'
  const icon = theme === 'light' ? <SunIcon size={14} /> : theme === 'dark' ? <MoonIcon size={14} /> : <MonitorIcon size={14} />
  const label = theme === 'light' ? t('theme.light') : theme === 'dark' ? t('theme.dark') : t('theme.auto')

  useEffect(() => {
    if (!exportOpen) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (exportRef.current?.contains(target)) return
      setExportOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setExportOpen(false)
      event.stopPropagation()
      event.stopImmediatePropagation()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [exportOpen])

  const openFile = async () => {
    const path = await pickSessionFileToOpen()
    if (path !== null) await importSession(path)
  }

  const exportAs = async (format: 'md' | 'html') => {
    setExportOpen(false)
    const path = await exportSession(format)
    if (path !== null) {
      useNoticeStore.getState().show({ kind: 'info', text: t('session.exported', { path }) })
    }
  }

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
      <div className={css.footerActions}>
        <Tooltip label={t('sidebar.import')}>
          <button
            type="button"
            className={css.footerButton}
            aria-label={t('sidebar.import')}
            onClick={() => void openFile()}
          >
            <FolderIcon size={14} />
          </button>
        </Tooltip>
        <div className={css.exportAnchor} ref={exportRef}>
          <Tooltip label={t('sidebar.export')}>
            <button
              type="button"
              className={css.footerButton}
              aria-label={t('sidebar.export')}
              aria-haspopup="menu"
              aria-expanded={exportOpen}
              onClick={() => setExportOpen((value) => !value)}
            >
              <FileIcon size={14} />
            </button>
          </Tooltip>
          {exportOpen && (
            <div className={css.exportMenu} role="menu" data-export-menu>
              <button
                type="button"
                role="menuitem"
                className={css.menuItem}
                onClick={() => void exportAs('md')}
              >
                {t('session.exportMarkdown')}
              </button>
              <button
                type="button"
                role="menuitem"
                className={css.menuItem}
                onClick={() => void exportAs('html')}
              >
                {t('session.exportHtml')}
              </button>
            </div>
          )}
        </div>
      </div>
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
