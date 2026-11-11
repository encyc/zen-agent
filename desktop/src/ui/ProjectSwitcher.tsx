/** The compact project switcher shared by the zen and swarm sidebars: a
 * trigger naming the active project, and a portal menu listing the registered
 * rail plus an open-folder action. Interaction mirrors the coding row menu
 * (focus first item, outside-click and Escape close, arrow-key cycling). */

import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { useAppStore } from '../state/appStore'
import { useProjectStore } from '../state/stores'
import { projectName } from '../state/sessionGroups'
import { pickProjectFolder } from './sessions/fileDialog'
import { useT } from '../i18n/locale'
import { CheckIcon, ChevronDownIcon, FolderIcon } from './icons'
import css from './ProjectSwitcher.module.css'

interface Anchor {
  left: number
  top: number
  width: number
}

export function ProjectSwitcher() {
  const t = useT()
  const project = useAppStore((state) => state.project)
  const openProject = useAppStore((state) => state.openProject)
  const projects = useProjectStore((state) => state.projects)
  const addProject = useProjectStore((state) => state.addProject)
  const [open, setOpen] = useState(false)
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  const close = (restoreFocus = false) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }

  const onTrigger = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    if (open) {
      setOpen(false)
      return
    }
    const rect = event.currentTarget.getBoundingClientRect()
    setAnchor({ left: rect.left, top: rect.bottom, width: rect.width })
    setOpen(true)
  }

  const choose = (path: string) => {
    setOpen(false)
    void openProject(path)
  }

  const openFolder = async () => {
    setOpen(false)
    const path = await pickProjectFolder()
    if (path !== null) {
      addProject(path)
      await openProject(path)
    }
  }

  useEffect(() => {
    if (!open) return
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (menuRef.current?.contains(target)) return
      if (triggerRef.current?.contains(target)) return
      setOpen(false)
    }
    // Capture phase: App's document listener would otherwise cancel the run
    // before this menu sees the Escape.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      close(true)
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

  const label = project !== null ? projectName(project) : t('sidebar.noProject')

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        className={css.trigger}
        data-project-switcher
        aria-label={t('sidebar.projectSwitcher')}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={onTrigger}
      >
        <FolderIcon size={13} />
        <span className={css.triggerLabel}>{label}</span>
        <ChevronDownIcon size={12} className={css.triggerChevron} />
      </button>

      {open && anchor !== null &&
        createPortal(
          <div
            ref={menuRef}
            className={css.menu}
            role="menu"
            data-project-switcher-menu
            style={{
              left: Math.max(8, anchor.left),
              top: anchor.top + 4,
              width: Math.max(anchor.width, 220),
            }}
            onKeyDown={(event) => {
              if (event.key === 'Tab') {
                event.preventDefault()
                close(true)
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
            {projects.map((entry) => {
              const isCurrent = project === entry.path
              return (
                <button
                  key={entry.path}
                  type="button"
                  role="menuitem"
                  className={css.item}
                  data-project-option={entry.path}
                  data-current={isCurrent || undefined}
                  onClick={() => choose(entry.path)}
                >
                  <span className={css.check}>{isCurrent ? <CheckIcon size={12} /> : null}</span>
                  <span className={css.itemMain}>
                    <span className={css.itemName}>{entry.name}</span>
                    <span className={css.itemPath}>{entry.path}</span>
                  </span>
                </button>
              )
            })}
            <div className={css.separator} role="separator" />
            <button
              type="button"
              role="menuitem"
              className={css.item}
              data-project-open-folder
              onClick={() => void openFolder()}
            >
              <span className={css.check}>
                <FolderIcon size={12} />
              </span>
              <span className={css.itemMain}>
                <span className={css.itemName}>{t('sidebar.openFolder')}</span>
              </span>
            </button>
          </div>,
          document.body,
        )}
    </>
  )
}