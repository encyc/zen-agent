/** Conversation chrome: project crumb · inline-rename title on the left, new
 * session and search on the right. The bar is a Tauri drag region; each control
 * is its own hit target. Session actions (undo, compact) live in the sidebar's
 * current-row menu, not here. */

import { useState, useRef, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useAppStore } from '../state/appStore'
import { usePaletteStore, useSessionStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { FolderIcon, PanelLeftIcon, PlusIcon, SearchIcon } from './icons'
import { Tooltip } from './primitives'
import css from './ConversationTopbar.module.css'

interface ConversationTopbarProps {
  collapsed: boolean
  onExpandSidebar(): void
  onNewSession(): void
}

export function ConversationTopbar(props: ConversationTopbarProps) {
  const t = useT()
  const sessions = useSessionStore((state) => state.sessions)
  const currentPath = useSessionStore((state) => state.currentPath)
  const current = sessions.find((session) => session.path === currentPath) ?? null
  const renameSession = useAppStore((state) => state.renameSession)

  const title = current?.title || current?.name || t('topbar.untitled')
  const [editing, setEditing] = useState(false)
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

  const onTitleKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      finishEditing(true)
    } else if (event.key === 'Escape') {
      event.stopPropagation()
      finishEditing(false)
    }
  }

  return (
    <div
      className={css.root}
      role="toolbar"
      aria-label={t('topbar.conversation')}
      data-tauri-drag-region
      data-collapsed={props.collapsed || undefined}
    >
      <div className={css.left} data-tauri-drag-region>
        {/* Out of flow: the expanded sidebar paints its own collapse control,
            so this seat only appears while the sidebar is collapsed. */}
        <div className={css.lead} aria-hidden={!props.collapsed}>
          <button
            type="button"
            className={css.button}
            onClick={props.onExpandSidebar}
            aria-label={t('topbar.expandSidebar')}
            tabIndex={props.collapsed ? undefined : -1}
          >
            <PanelLeftIcon size={15} />
          </button>
        </div>
        <div className={css.titleWrap} data-tauri-drag-region>
          {current?.project ? (
            <>
              <span className={css.project} data-topbar-project>
                <FolderIcon size={12} />
                <span className={css.projectName}>{current.project}</span>
              </span>
              <span className={css.sep} data-topbar-sep aria-hidden>
                /
              </span>
            </>
          ) : null}
          {editing ? (
            <input
              ref={inputRef}
              className={css.titleInput}
              aria-label={t('topbar.title')}
              defaultValue={title}
              spellCheck={false}
              autoFocus
              onBlur={() => finishEditing(false)}
              onKeyDown={onTitleKeyDown}
            />
          ) : (
            <h1 className={css.titleHeading}>
              <button
                type="button"
                className={css.titleButton}
                data-topbar-title
                aria-label={t('topbar.rename')}
                onClick={startEditing}
                title={title}
              >
                {title}
              </button>
            </h1>
          )}
        </div>
      </div>
      <div className={css.right}>
        <button
          type="button"
          className={css.button}
          onClick={props.onNewSession}
          aria-label={t('topbar.new')}
        >
          <PlusIcon size={15} />
        </button>
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
      </div>
    </div>
  )
}
