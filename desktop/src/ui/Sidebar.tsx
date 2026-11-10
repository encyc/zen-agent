/** Left sidebar with pi's information architecture: a brand header, a pinned
 * section, a standalone sessions section with search and file actions, and
 * collapsible project groups paginated ten rows at a time. Session rows keep
 * the dsh pattern: hover-revealed menu trigger, inline rename, two-step delete. */

import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { useAppStore } from '../state/appStore'
import { canForkSession, isBlockedSession } from '../state/sessionSelectors'
import {
  currentPending,
  useNoticeStore,
  useProjectStore,
  useSessionStore,
  useSettingsStore,
  useStatusStore,
} from '../state/stores'
import {
  mergeProjects,
  projectRowCount,
  sessionSections,
  type SidebarProjectGroup,
} from '../state/sessionGroups'
import { armDelete, deleteArmed } from '../state/armedDelete'
import { useT } from '../i18n/locale'
import type { SessionSummary } from '../rpc/types'
import { Tooltip } from './primitives'
import {
  ChatIcon,
  ChevronDownIcon,
  FileIcon,
  FolderIcon,
  ForkIcon,
  MonitorIcon,
  MoonIcon,
  MoreIcon,
  PanelLeftIcon,
  PinIcon,
  PlusIcon,
  SettingsIcon,
  SunIcon,
  XIcon,
} from './icons'
import { formatAge } from './format'
import { Notifications } from './Notifications'
import { captureChatState } from './pages/navigation'
import { pickProjectFolder, pickSessionFileToOpen } from './sessions/fileDialog'
import { AppMark } from './AppMark'
import { RunningMark } from './RunningMark'
import { ZenWordmark } from './ZenWordmark'
import css from './Sidebar.module.css'

interface RowMenu {
  path: string
  left: number
  top: number
}

const SECTION_KEY = 'zen.sidebar.sections'

function readClosedSections(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SECTION_KEY) ?? '[]')
    return Array.isArray(value)
      ? value.filter((entry): entry is string => typeof entry === 'string')
      : []
  } catch {
    return []
  }
}

export function Sidebar(props: { onCollapse(): void }) {
  const t = useT()
  const sessions = useSessionStore((state) => state.sessions)
  const currentPath = useSessionStore((state) => state.currentPath)
  const collapsedProjects = useSessionStore((state) => state.collapsedProjects)
  const toggleProject = useSessionStore((state) => state.toggleProject)
  const projectEntries = useProjectStore((state) => state.projects)
  const addProject = useProjectStore((state) => state.addProject)
  const removeProject = useProjectStore((state) => state.removeProject)
  const openProject = useAppStore((state) => state.openProject)
  const newSession = useAppStore((state) => state.newSession)
  const renameSession = useAppStore((state) => state.renameSession)
  const togglePin = useAppStore((state) => state.togglePin)
  const deleteSession = useAppStore((state) => state.deleteSession)
  const forkSession = useAppStore((state) => state.forkSession)
  const openSession = useAppStore((state) => state.openSession)
  const undoLast = useAppStore((state) => state.undoLast)
  const compactNow = useAppStore((state) => state.compactNow)
  const busy = useAppStore((state) => state.busy)
  const pending = useStatusStore((state) => currentPending(state.status).length)
  const [query, setQuery] = useState('')
  const [menu, setMenu] = useState<RowMenu | null>(null)
  const [armed, setArmed] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  // Pages revealed per project: one on open, +1 per "more" click. Keyed by
  // project so an expand survives a session-list refresh.
  const [projectPages, setProjectPages] = useState<Record<string, number>>({})
  const [closedSections, setClosedSections] = useState<string[]>(readClosedSections)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  const { pinned, standalone, projects } = useMemo(() => {
    const sections = sessionSections(sessions, query)
    return {
      pinned: sections.pinned,
      standalone: sections.standalone,
      projects: mergeProjects(sections.projects, projectEntries, query),
    }
  }, [sessions, query, projectEntries])
  const current = sessions.find((session) => session.path === currentPath) ?? null
  const currentProject = current?.project ?? null
  const registeredProjects = useMemo(
    () => new Set(projectEntries.map((entry) => entry.path)),
    [projectEntries],
  )
  const menuSession = menu === null ? null : sessions.find((session) => session.path === menu.path) ?? null
  const empty = pinned.length === 0 && standalone.length === 0 && projects.length === 0

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

  const toggleSection = (name: string) => {
    setClosedSections((value) => {
      const next = value.includes(name)
        ? value.filter((entry) => entry !== name)
        : [...value, name]
      try {
        localStorage.setItem(SECTION_KEY, JSON.stringify(next))
      } catch {
        // ignore
      }
      return next
    })
  }
  const sessionsClosed = closedSections.includes('sessions')
  const projectsClosed = closedSections.includes('projects')

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

  const renderRow = (session: SessionSummary, showProject = false) => (
    <SessionRow
      key={session.path}
      path={session.path}
      title={session.title || session.name}
      messages={session.messages}
      updatedAt={session.updated_at}
      model={session.model}
      project={session.project}
      derivedFrom={session.derived_from}
      showProject={showProject}
      current={session.path === currentPath}
      pinned={session.pinned === true}
      blocked={isBlockedSession(session, currentPath)}
      busy={busy}
      pending={session.path === currentPath ? pending : 0}
      editing={editing === session.path}
      menuOpen={menu?.path === session.path}
      onOpen={() => void openSession(session.path)}
      onMenu={(event) => openMenu(event, session.path)}
      onRename={(title) => commitRename(session.path, title)}
      onCancelRename={() => setEditing(null)}
    />
  )

  const createProject = async () => {
    const path = await pickProjectFolder()
    if (path === null) return
    addProject(path)
    await openProject(path)
  }

  const newSessionIn = async (path: string) => {
    // A deliberate new conversation: boot that project fresh rather than
    // resuming its latest and switching away one frame later.
    await openProject(path, null)
    await newSession()
  }

  const renderProject = (group: SidebarProjectGroup) => {
    const collapsed = collapsedProjects.includes(group.project)
    const pages = projectPages[group.project] ?? 1
    const rows = group.sessions.slice(0, projectRowCount(group.sessions.length, pages))
    const hidden = group.sessions.length - rows.length
    const active = group.project === currentProject
    return (
      <section
        key={group.project}
        className={css.projectGroup}
        data-project-group={group.project}
        data-sidebar-project-group={group.project}
        aria-labelledby={`project-${group.project}`}
      >
        <div className={css.projectHeader}>
          <button
            type="button"
            id={`project-${group.project}`}
            className={css.projectToggle}
            aria-label={t('sidebar.projectGroup', { name: group.project })}
            aria-expanded={!collapsed}
            onClick={() => toggleProject(group.project)}
          >
            <ChevronDownIcon
              size={13}
              className={css.projectChevron}
              data-collapsed={collapsed || undefined}
            />
            <FolderIcon size={13} />
            <span className={css.projectName}>{group.project}</span>
            {active && (
              <span className={css.projectActiveDot} title={t('sidebar.projectActive')} />
            )}
          </button>
          {group.path !== null && (
            <span className={css.projectActions}>
              <button
                type="button"
                className={css.projectAction}
                data-new-session
                aria-label={t('sidebar.newInProject', { name: group.project })}
                onClick={() => void newSessionIn(group.path as string)}
              >
                <PlusIcon size={12} />
              </button>
              {registeredProjects.has(group.path) && (
                <button
                  type="button"
                  className={css.projectAction}
                  data-remove-project
                  aria-label={t('sidebar.removeProject', { name: group.project })}
                  onClick={() => removeProject(group.path as string)}
                >
                  <XIcon size={12} />
                </button>
              )}
            </span>
          )}
        </div>
        <div
          className={css.projectBody}
          hidden={collapsed}
          aria-hidden={collapsed || undefined}
          {...(collapsed ? ({ inert: '' } as Record<string, string>) : {})}
        >
          {rows.map((session) => renderRow(session))}
          {rows.length === 0 && (
            <div className={css.sectionEmpty}>{t('sidebar.projectEmpty')}</div>
          )}
          {hidden > 0 && (
            <button
              type="button"
              className={css.loadMore}
              onClick={() =>
                setProjectPages((value) => ({
                  ...value,
                  [group.project]: (value[group.project] ?? 1) + 1,
                }))
              }
            >
              {t('sidebar.more', { count: hidden })}
            </button>
          )}
        </div>
      </section>
    )
  }

  return (
    <div className={css.root} data-shell-sidebar>
      <div className={css.header} data-tauri-drag-region>
        <div className={css.brand} data-tauri-drag-region>
          <div className={css.brandMark} aria-hidden>
            <AppMark alt="" draggable={false} />
          </div>
          <ZenWordmark height={13} />
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

      {pinned.length > 0 && (
        <section className={css.pinned} data-sidebar-section="pinned" aria-labelledby="pinned-label">
          <div className={css.sectionToolbar}>
            <span id="pinned-label" className={css.sectionLabel}>{t('sidebar.pinned')}</span>
          </div>
          <div className={css.pinnedList}>{pinned.map((session) => renderRow(session, true))}</div>
        </section>
      )}

      <section className={css.sessions} data-sidebar-section="sessions" aria-labelledby="sessions-label">
        <div className={css.sectionToolbar}>
          <button
            type="button"
            className={css.sectionToggle}
            aria-expanded={!sessionsClosed}
            aria-label={t(sessionsClosed ? 'sidebar.expandSection' : 'sidebar.collapseSection', {
              name: t('sidebar.sessions'),
            })}
            onClick={() => toggleSection('sessions')}
          >
            <ChevronDownIcon
              size={12}
              className={css.sectionChevron}
              data-collapsed={sessionsClosed || undefined}
            />
            <span id="sessions-label" className={css.sectionLabel}>{t('sidebar.sessions')}</span>
          </button>
          <SessionTools />
        </div>
        {!sessionsClosed && (
          <>
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
            <div className={css.standaloneList}>
              {standalone.length > 0 ? (
                standalone.map((session) => renderRow(session))
              ) : (
                <div className={css.sectionEmpty}>{t('sidebar.standalone')}</div>
              )}
            </div>
          </>
        )}
      </section>

      <div className={css.sectionToolbar} data-sidebar-section="projects">
        <button
          type="button"
          className={css.sectionToggle}
          aria-expanded={!projectsClosed}
          aria-label={t(projectsClosed ? 'sidebar.expandSection' : 'sidebar.collapseSection', {
            name: t('sidebar.projects'),
          })}
          onClick={() => toggleSection('projects')}
        >
          <ChevronDownIcon
            size={12}
            className={css.sectionChevron}
            data-collapsed={projectsClosed || undefined}
          />
          <span className={css.sectionLabel}>{t('sidebar.projects')}</span>
        </button>
        <Tooltip label={t('sidebar.newProject')}>
          <button
            type="button"
            className={css.toolbarButton}
            aria-label={t('sidebar.newProject')}
            onClick={() => void createProject()}
          >
            <PlusIcon size={13} />
          </button>
        </Tooltip>
      </div>
      {!projectsClosed && (
        <div className={css.projectList}>
          {projects.map(renderProject)}
          {empty && (
            <div className={css.empty}>
              <ChatIcon size={16} />
              <span>{t('sidebar.empty')}</span>
            </div>
          )}
        </div>
      )}

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
                <button
                  type="button"
                  role="menuitem"
                  className={css.menuItem}
                  disabled={busy}
                  onClick={() => {
                    setMenu(null)
                    void undoLast()
                  }}
                >
                  {t('session.undo')}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={css.menuItem}
                  disabled={busy}
                  onClick={() => {
                    setMenu(null)
                    void compactNow()
                  }}
                >
                  {t('session.compact')}
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

function SessionTools() {
  const t = useT()
  const importSession = useAppStore((state) => state.importSession)
  const exportSession = useAppStore((state) => state.exportSession)
  const [exportOpen, setExportOpen] = useState(false)
  const exportRef = useRef<HTMLDivElement | null>(null)

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
    <div className={css.toolbarActions}>
      <Tooltip label={t('sidebar.import')}>
        <button
          type="button"
          className={css.toolbarButton}
          aria-label={t('sidebar.import')}
          onClick={() => void openFile()}
        >
          <FolderIcon size={13} />
        </button>
      </Tooltip>
      <div className={css.exportAnchor} ref={exportRef}>
        <Tooltip label={t('sidebar.export')}>
          <button
            type="button"
            className={css.toolbarButton}
            aria-label={t('sidebar.export')}
            aria-haspopup="menu"
            aria-expanded={exportOpen}
            onClick={() => setExportOpen((value) => !value)}
          >
            <FileIcon size={13} />
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
      <Tooltip label={t('sidebar.new')}>
        <button
          type="button"
          className={css.toolbarButton}
          aria-label={t('sidebar.new')}
          onClick={() => void useAppStore.getState().newSession()}
        >
          <PlusIcon size={14} />
        </button>
      </Tooltip>
    </div>
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
  showProject: boolean
  current: boolean
  pinned: boolean
  blocked: boolean
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
  const sessionTime = useSettingsStore((state) => state.sessionTime)
  const lineage = props.derivedFrom
    ? t('session.forkedFrom', { name: props.derivedFrom })
    : null
  const sub = [
    `${props.messages} ${t('session.messages')}`,
    props.showProject ? props.project ?? '' : '',
    props.model ?? '',
    sessionTime ? formatAge(props.updatedAt, locale) : '',
  ]
    .filter((part) => part !== '')
    .join(' · ')
  return (
    <div
      className={css.sessionRow}
      data-session-path={props.path}
      data-current={props.current || undefined}
      data-blocked={props.blocked || undefined}
      aria-disabled={props.blocked || undefined}
      onClick={props.blocked ? undefined : props.onOpen}
      role="button"
      tabIndex={0}
      title={props.blocked ? t('sidebar.blockedSession') : lineage ?? props.title}
      onKeyDown={(event) => {
        // Nested controls (the menu trigger, the rename input) handle their own
        // Enter; only the focused row itself opens the session.
        if (event.key === 'Enter' && !props.blocked && event.target === event.currentTarget) {
          props.onOpen()
        }
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
          <RunningMark size={8} />
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
  const setView = useSettingsStore((state) => state.setView)
  const theme = useSettingsStore((state) => state.theme)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const openSettings = () => {
    captureChatState()
    setView('settings')
  }

  const nextTheme = theme === 'light' ? 'dark' : theme === 'dark' ? 'auto' : 'light'
  const themeLabel =
    theme === 'light' ? t('theme.light') : theme === 'dark' ? t('theme.dark') : t('theme.auto')
  const themeIcon =
    theme === 'light' ? (
      <SunIcon size={14} />
    ) : theme === 'dark' ? (
      <MoonIcon size={14} />
    ) : (
      <MonitorIcon size={14} />
    )

  return (
    <div className={css.footer} data-sidebar-footer>
      <div className={css.footerActions}>
        <Tooltip label={t('settings.title')}>
          <button
            type="button"
            className={css.footerButton}
            aria-label={t('settings.title')}
            onClick={openSettings}
          >
            <SettingsIcon size={14} />
          </button>
        </Tooltip>
        <Tooltip label={`${t('menu.theme')}: ${themeLabel}`}>
          <button
            type="button"
            className={css.footerButton}
            aria-label={`${t('menu.theme')}: ${themeLabel}`}
            onClick={() => setTheme(nextTheme)}
          >
            {themeIcon}
          </button>
        </Tooltip>
        <Notifications />
      </div>
      <span className={css.version}>v{__APP_VERSION__}</span>
    </div>
  )
}
