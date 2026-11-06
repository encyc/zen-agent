/** Home hero: the empty-conversation surface shared by the idle welcome screen
 * and an empty connected transcript. The headline embeds a project switcher;
 * three onboarding tips stay until dismissed. */

import { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { useT } from '../i18n/locale'
import { FolderIcon, XIcon } from './icons'
import css from './HomeHero.module.css'

const TIPS_KEY = 'zen.home.tips'

export interface RecentProject {
  path: string
  at: number
}

export interface HomeHeroProps {
  connected: boolean
  projectName: string | null
  recents: RecentProject[]
  onPickFolder(): void
  onUseCurrent(): void
  onOpenRecent(path: string): void
}

export async function pickProjectFolder(): Promise<string | null> {
  try {
    const selection = await invoke<string | null>('plugin:dialog|open', {
      options: { directory: true, multiple: false, title: 'ZenAgent' },
    })
    return typeof selection === 'string' ? selection : null
  } catch {
    return null
  }
}

function baseName(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

export function HomeHero(props: HomeHeroProps) {
  const t = useT()
  const [menuOpen, setMenuOpen] = useState(false)
  const [tips, setTips] = useState(() => {
    try {
      return localStorage.getItem(TIPS_KEY) !== 'off'
    } catch {
      return true
    }
  })
  const anchorRef = useRef<HTMLSpanElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!menuOpen) return
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    const onPointerDown = (event: PointerEvent) => {
      if (anchorRef.current?.contains(event.target as Node)) return
      setMenuOpen(false)
    }
    // Capture phase: App's Escape cancels the run and must not see this one.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      event.stopPropagation()
      event.stopImmediatePropagation()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [menuOpen])

  const dismissTips = () => {
    setTips(false)
    try {
      localStorage.setItem(TIPS_KEY, 'off')
    } catch {
      // ignore
    }
  }

  const choose = (action: () => void) => {
    setMenuOpen(false)
    action()
  }

  return (
    <div className={css.root} data-home-hero>
      <div className={css.mark} aria-hidden>
        <HeroMark />
      </div>
      <h1 className={css.headline}>
        {props.connected ? t('home.titleInProjectBefore') : t('home.titleIdleBefore')}
        <span className={css.projectAnchor} ref={anchorRef}>
          <button
            type="button"
            className={css.projectButton}
            aria-label={t('home.switchProject')}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
          >
            {props.projectName ?? t('home.chooseProject')}
          </button>
          {menuOpen && (
            <div ref={menuRef} className={css.menu} role="menu">
              {props.connected && props.projectName !== null && (
                <div className={css.currentProject}>
                  {t('home.currentProject')} · {props.projectName}
                </div>
              )}
              {props.recents.length > 0 && <div className={css.menuLabel}>{t('home.recents')}</div>}
              {props.recents.map((recent) => (
                <button
                  key={recent.path}
                  type="button"
                  role="menuitem"
                  className={css.menuItem}
                  title={recent.path}
                  onClick={() => choose(() => props.onOpenRecent(recent.path))}
                >
                  <FolderIcon size={13} />
                  <span className={css.menuPath}>{baseName(recent.path)}</span>
                </button>
              ))}
              <button
                type="button"
                role="menuitem"
                className={css.menuItem}
                onClick={() => choose(props.onPickFolder)}
              >
                <FolderIcon size={13} />
                <span>{t('home.openOther')}</span>
              </button>
              {!props.connected && (
                <button
                  type="button"
                  role="menuitem"
                  className={css.menuItem}
                  onClick={() => choose(props.onUseCurrent)}
                >
                  {t('home.useCurrent')}
                </button>
              )}
            </div>
          )}
        </span>
        {props.connected ? t('home.titleInProjectAfter') : t('home.titleIdleAfter')}
      </h1>
      {tips && (
        <div className={css.tips} data-home-tips>
          <div className={css.tipsHeader}>
            <span>{t('home.tipsTitle')}</span>
            <button
              type="button"
              className={css.dismiss}
              aria-label={t('home.dismissTips')}
              onClick={dismissTips}
            >
              <XIcon size={11} />
            </button>
          </div>
          <div className={css.tipRow}>
            <kbd className={css.kbd}>⌘K</kbd>
            <span>{t('home.tipSearch')}</span>
          </div>
          <div className={css.tipRow}>
            <kbd className={css.kbd}>/</kbd>
            <span>{t('home.tipCommand')}</span>
          </div>
          <div className={css.tipRow}>
            <kbd className={css.kbd}>⌘B</kbd>
            <span>{t('home.tipSidebar')}</span>
          </div>
        </div>
      )}
    </div>
  )
}

function HeroMark() {
  return (
    <svg width="100" height="100" viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="1" y="1" width="16" height="16" rx="4.5" />
      <path d="M9 4.2 10.3 7.7 13.8 9l-3.5 1.3L9 13.8 7.7 10.3 4.2 9l3.5-1.3Z" />
    </svg>
  )
}
