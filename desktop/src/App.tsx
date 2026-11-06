/** App shell: theme presenter, connection gate (welcome → boot → frame →
 * exit banner). Keyboard: Cmd/Ctrl+B toggles the sidebar, like dsh. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { lastProjectCwd, useAppStore } from './state/appStore'
import { useApprovalStore, usePaletteStore, useSettingsStore } from './state/stores'
import { useT } from './i18n/locale'
import { AppFrame } from './ui/AppFrame'
import { Sidebar } from './ui/Sidebar'
import { ChatView } from './ui/chat/ChatView'
import { InputBar } from './ui/composer/InputBar'
import { RightPanel } from './ui/rightbar/RightPanel'
import { WelcomeScreen } from './ui/WelcomeScreen'
import { ConversationTopbar } from './ui/ConversationTopbar'
import { NoticeBanner } from './ui/NoticeBanner'
import { CommandPalette } from './ui/palette/CommandPalette'
import { SIDEBAR_DEFAULT, RIGHTBAR_DEFAULT, clampChatWidth } from './ui/columns'
import { PanelRightIcon } from './ui/icons'
import css from './App.module.css'

const SIDEBAR_KEY = 'zen.sidebar.width'
const RIGHTBAR_KEY = 'zen.rightbar.width'

function loadWidth(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key)
    const value = raw === null ? NaN : Number(raw)
    return Number.isFinite(value) && value > 0 ? value : fallback
  } catch {
    return fallback
  }
}

export function App() {
  const t = useT()
  const connection = useAppStore((state) => state.connection)
  const connect = useAppStore((state) => state.connect)
  const collapsed = useSettingsStore((state) => state.sidebarCollapsed)
  const setCollapsed = useSettingsStore((state) => state.setSidebarCollapsed)
  const theme = useSettingsStore((state) => state.theme)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  // A returning user goes straight back to the last project; only the idle
  // host asks for a folder when no run has ever succeeded.
  const [lastCwd] = useState(lastProjectCwd)
  const autoConnected = useRef(false)

  const [sidebarWidth, setSidebarWidth] = useState(() => loadWidth(SIDEBAR_KEY, SIDEBAR_DEFAULT))
  const [rightbarOpen, setRightbarOpen] = useState(false)
  const [rightbarWidth, setRightbarWidth] = useState(() => loadWidth(RIGHTBAR_KEY, RIGHTBAR_DEFAULT))

  // Theme presenter: write the snapshot to the DOM, outside React (dsh pattern).
  useEffect(() => {
    const apply = () => {
      const dark =
        theme === 'dark' ||
        (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches)
      document.body.toggleAttribute('data-ds-dark-theme', dark)
    }
    apply()
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [theme])

  // The band width is a shell-wide custom property: both the transcript and
  // the docked composer read it.
  useEffect(() => {
    document.documentElement.style.setProperty(
      '--ds-chat-content-width',
      `${clampChatWidth(chatWidth)}px`,
    )
  }, [chatWidth])

  // Platform flag (dsh: html[data-platform]) drives macOS chrome adjustments.
  useEffect(() => {
    const platform = navigator.userAgent.includes('Mac') ? 'darwin' : navigator.userAgent.includes('Win') ? 'windows' : 'linux'
    document.documentElement.dataset.platform = platform
  }, [])

  useEffect(() => {
    if (connection !== 'idle' || lastCwd === '' || autoConnected.current) return
    autoConnected.current = true
    void connect(undefined, lastCwd)
  }, [connection, lastCwd, connect])

  const persistWidth = useCallback((key: string, value: number) => {
    try {
      localStorage.setItem(key, String(Math.round(value)))
    } catch {
      // ignore
    }
  }, [])

  // Global shortcuts (dsh fixed shortcuts, desktop flavor). Esc means
  // "interrupt the run" only when no approval owns it (the panel maps Esc to
  // reject, which must not also cancel the run).
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'b') {
        event.preventDefault()
        setCollapsed(!collapsed)
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        usePaletteStore.getState().openPalette()
      }
      // While the palette is open it owns Enter and Escape.
      if (usePaletteStore.getState().open) return
      if (event.key === 'Escape' && useAppStore.getState().busy) {
        if (useApprovalStore.getState().queue.length > 0) return
        void useAppStore.getState().cancel()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [collapsed, setCollapsed])

  if (connection === 'idle') {
    if (lastCwd !== '') return <BootScreen />
    // No explicit args: the app store owns the version-compatible defaults.
    return <WelcomeScreen onStart={(cwd) => void connect(undefined, cwd)} />
  }

  if (connection === 'starting') {
    return <BootScreen />
  }

  if (connection === 'exited') {
    return (
      <ExitScreen
        onRestart={() =>
          void useAppStore.getState().connect(undefined, lastCwd === '' ? undefined : lastCwd)
        }
      />
    )
  }

  return (
    <>
      <AppFrame
      sidebarOpen={!collapsed}
      sidebarWidth={sidebarWidth}
      onSidebarWidth={(width) => {
        setSidebarWidth(width)
        persistWidth(SIDEBAR_KEY, width)
      }}
      onSidebarToggle={() => setCollapsed(true)}
      rightbarOpen={rightbarOpen}
      rightbarWidth={rightbarWidth}
      onRightbarWidth={(width) => {
        setRightbarWidth(width)
        persistWidth(RIGHTBAR_KEY, width)
      }}
      sidebar={<Sidebar onCollapse={() => setCollapsed(true)} />}
      rightbar={<RightPanel />}
    >
      <ConversationTopbar
        collapsed={collapsed}
        onExpandSidebar={() => setCollapsed(false)}
        onNewSession={() => void useAppStore.getState().newSession()}
      />
      <NoticeBanner />
      <ChatView />
      <div className={css.composerDock} data-shell-composer>
        <InputBar />
      </div>
      <button
        type="button"
        className={css.workPanelToggle}
        aria-label={t('topbar.inspector')}
        aria-expanded={rightbarOpen}
        data-active={rightbarOpen || undefined}
        onClick={() => setRightbarOpen((value) => !value)}
      >
        <PanelRightIcon size={15} />
      </button>
      </AppFrame>
      <CommandPalette />
    </>
  )
}

function BootScreen() {
  const t = useT()
  return (
    <div className={css.bootScreen}>
      <div className={css.bootSpinner} aria-hidden />
      <span>{t('connection.starting')}</span>
    </div>
  )
}

function ExitScreen({ onRestart }: { onRestart(): void }) {
  const t = useT()
  const exitCode = useAppStore((state) => state.exitCode)
  const stderrTail = useAppStore((state) => state.stderrTail)
  const tail = stderrTail.slice(-6).join('\n')
  return (
    <div className={css.bootScreen}>
      <div className={css.exitCard}>
        <div className={css.exitTitle}>{t('connection.exited')}{exitCode !== null ? ` (${exitCode})` : ''}</div>
        {tail !== '' && <pre className={css.exitTail}>{tail}</pre>}
        <button type="button" className={css.exitButton} onClick={onRestart}>
          {t('connection.restart')}
        </button>
      </div>
    </div>
  )
}
