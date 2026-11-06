/** Conversation chrome: project · session title (inline rename), new session,
 * session menu and the inspector toggle. The bar is a Tauri drag region; each
 * control is its own hit target. */

import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../state/appStore'
import { usePaletteStore, useSessionStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { MoreIcon, PanelLeftIcon, PanelRightIcon, PencilIcon, PlusIcon, SearchIcon } from './icons'
import { Tooltip } from './primitives'
import css from './ConversationTopbar.module.css'

interface ConversationTopbarProps {
  collapsed: boolean
  onExpandSidebar(): void
  onToggleInspector(): void
  inspectorOpen: boolean
  onNewSession(): void
}

export function ConversationTopbar(props: ConversationTopbarProps) {
  const t = useT()
  const sessions = useSessionStore((state) => state.sessions)
  const currentPath = useSessionStore((state) => state.currentPath)
  const current = sessions.find((session) => session.path === currentPath) ?? null
  const renameSession = useAppStore((state) => state.renameSession)
  const undoLast = useAppStore((state) => state.undoLast)
  const compactNow = useAppStore((state) => state.compactNow)
  const busy = useAppStore((state) => state.busy)

  const title = current?.title || current?.name || t('topbar.untitled')
  const [editing, setEditing] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const settled = useRef(false)

  const startEditing = () => {
    settled.current = false
    setEditing(true)
  }

  const finishEditing = (commit: boolean) => {
    if (settled.current) return
    settled.current = true
    setEditing(false)
    if (!commit) return
    const next = inputRef.current?.value.trim() ?? ''
    if (next !== '' && next !== title) void renameSession(next)
  }

  useEffect(() => {
    if (!menuOpen) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest('[data-topbar-menu-anchor]')) return
      setMenuOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [menuOpen])

  return (
    <div
      className={css.root}
      data-tauri-drag-region
      data-collapsed={props.collapsed || undefined}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && menuOpen) {
          event.stopPropagation()
          setMenuOpen(false)
        }
      }}
    >
      {props.collapsed && (
        <button
          type="button"
          className={css.button}
          onClick={props.onExpandSidebar}
          aria-label={t('topbar.expandSidebar')}
        >
          <PanelLeftIcon size={15} />
        </button>
      )}
      {current?.project ? (
        <span className={css.project} data-topbar-project>
          {current.project}
        </span>
      ) : null}
      {editing ? (
        <input
          ref={inputRef}
          className={css.titleInput}
          aria-label={t('topbar.title')}
          defaultValue={title}
          autoFocus
          onBlur={() => finishEditing(false)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              finishEditing(true)
            } else if (event.key === 'Escape') {
              event.stopPropagation()
              finishEditing(false)
            }
          }}
        />
      ) : (
        <button
          type="button"
          className={css.titleButton}
          data-topbar-title
          aria-label={t('topbar.rename')}
          onClick={startEditing}
          title={title}
        >
          <span className={css.titleText}>{title}</span>
          <PencilIcon size={12} className={css.titlePencil} />
        </button>
      )}
      <div className={css.spacer} data-tauri-drag-region />
      <Tooltip label="⌘K">
        <button
          type="button"
          className={css.button}
          aria-label={t('topbar.search')}
          onClick={() => usePaletteStore.getState().openPalette()}
        >
          <SearchIcon size={15} />
        </button>
      </Tooltip>
      <button
        type="button"
        className={css.button}
        onClick={props.onNewSession}
        aria-label={t('topbar.new')}
      >
        <PlusIcon size={15} />
      </button>
      <div className={css.menuAnchor} data-topbar-menu-anchor>
        <button
          type="button"
          className={css.button}
          data-topbar-menu
          aria-label={t('topbar.more')}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
        >
          <MoreIcon size={15} />
        </button>
        {menuOpen && (
          <div className={css.menu} role="menu">
            <button
              type="button"
              role="menuitem"
              className={css.menuItem}
              disabled={busy}
              onClick={() => {
                setMenuOpen(false)
                void undoLast()
              }}
            >
              {t('topbar.undo')}
            </button>
            <button
              type="button"
              role="menuitem"
              className={css.menuItem}
              disabled={busy}
              onClick={() => {
                setMenuOpen(false)
                void compactNow()
              }}
            >
              {t('topbar.compact')}
            </button>
          </div>
        )}
      </div>
      <button
        type="button"
        className={css.button}
        onClick={props.onToggleInspector}
        aria-label={t('topbar.inspector')}
        data-active={props.inspectorOpen || undefined}
      >
        <PanelRightIcon size={15} />
        <span className={css.dot} data-on={busy || undefined} />
      </button>
    </div>
  )
}
