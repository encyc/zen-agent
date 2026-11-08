/** App shell: theme presenter, connection gate (welcome → boot → frame →
 * exit banner). Keyboard: Cmd/Ctrl+B toggles the sidebar, like dsh. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { defaultProjectCwd, lastProjectCwd, useAppStore } from './state/appStore'
import {
  clampFontScale,
  useApprovalStore,
  usePaletteStore,
  useSettingsStore,
  useWorkPanelStore,
} from './state/stores'
import { useT } from './i18n/locale'
import { AppFrame } from './ui/AppFrame'
import { Sidebar } from './ui/Sidebar'
import { ChatView } from './ui/chat/ChatView'
import { InputBar } from './ui/composer/InputBar'
import { RightPanel } from './ui/rightbar/RightPanel'
import { SettingsPage } from './ui/pages/SettingsPage'
import { ToolsPage } from './ui/pages/ToolsPage'
import { restoreChatState } from './ui/pages/navigation'
import { isRuntimeMissing, type RuntimeProbe } from './state/startFailure.ts'
import { ConversationTopbar } from './ui/ConversationTopbar'
import { NoticeBanner } from './ui/NoticeBanner'
import { UpdateBanner } from './ui/UpdateBanner'
import { useUpdateStore } from './state/updateStore'
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
  const switching = useAppStore((state) => state.switching)
  const connect = useAppStore((state) => state.connect)
  const collapsed = useSettingsStore((state) => state.sidebarCollapsed)
  const setCollapsed = useSettingsStore((state) => state.setSidebarCollapsed)
  const theme = useSettingsStore((state) => state.theme)
  const themeId = useSettingsStore((state) => state.themeId)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  const fontScale = useSettingsStore((state) => state.fontScale)
  const view = useSettingsStore((state) => state.view)
  const previousView = useRef(view)
  const updateChecked = useRef(false)
  // The shell opens straight into a chat: the last project, else the user's
  // home folder, else zen's own cwd. There is no idle welcome surface.
  const [lastCwd] = useState(lastProjectCwd)
  const autoConnected = useRef(false)

  const [sidebarWidth, setSidebarWidth] = useState(() => loadWidth(SIDEBAR_KEY, SIDEBAR_DEFAULT))
  const rightbarOpen = useWorkPanelStore((state) => state.open)
  const [rightbarWidth, setRightbarWidth] = useState(() => loadWidth(RIGHTBAR_KEY, RIGHTBAR_DEFAULT))
  const dockRef = useRef<HTMLDivElement | null>(null)

  // Theme presenter: write the snapshot to the DOM, outside React (dsh pattern).
  // The palette rides a separate attribute so both sides of every palette
  // resolve against the same mode flip.
  useEffect(() => {
    const apply = () => {
      const dark =
        theme === 'dark' ||
        (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches)
      document.body.toggleAttribute('data-ds-dark-theme', dark)
    }
    document.body.dataset.dsTheme = themeId
    apply()
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [theme, themeId])

  // The band width is a shell-wide custom property: both the transcript and
  // the docked composer read it.
  useEffect(() => {
    document.documentElement.style.setProperty(
      '--ds-chat-content-width',
      `${clampChatWidth(chatWidth)}px`,
    )
  }, [chatWidth])

  useEffect(() => {
    document.documentElement.style.setProperty(
      '--ds-font-scale',
      String(clampFontScale(fontScale)),
    )
  }, [fontScale])

  // Returning from Settings/Tools re-applies the scroll offset and composer
  // focus captured when the page opened; the chat view itself remounts.
  useEffect(() => {
    if (previousView.current !== 'chat' && view === 'chat') restoreChatState()
    previousView.current = view
  }, [view])

  // Platform flag (dsh: html[data-platform]) drives macOS chrome adjustments.
  useEffect(() => {
    const platform = navigator.userAgent.includes('Mac') ? 'darwin' : navigator.userAgent.includes('Win') ? 'windows' : 'linux'
    document.documentElement.dataset.platform = platform
  }, [])

  useEffect(() => {
    if (connection !== 'idle' || autoConnected.current) return
    autoConnected.current = true
    void (async () => {
      const cwd = lastCwd !== '' ? lastCwd : await defaultProjectCwd()
      await connect(undefined, cwd)
    })()
  }, [connection, lastCwd, connect])

  // One update check per launch, five seconds after the shell is usable: the
  // cached answer shows immediately, the network answer arrives later.
  useEffect(() => {
    if (connection !== 'ready' || updateChecked.current) return
    updateChecked.current = true
    void useUpdateStore.getState().start()
  }, [connection])

  // The composer floats over the center column; publish its real height so
  // the transcript reserves exactly that much and nothing is covered.
  useEffect(() => {
    if (connection !== 'ready') return
    const dock = dockRef.current
    const pane = dock?.closest<HTMLElement>('[data-shell-center]')
    if (dock === null || dock === undefined || pane === null || pane === undefined) return
    const publish = () => {
      pane.style.setProperty(
        '--composer-dock-height',
        `${Math.round(dock.getBoundingClientRect().height)}px`,
      )
    }
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(dock)
    return () => observer.disconnect()
  }, [connection])

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
    return <BootScreen />
  }

  if (connection === 'starting') {
    return <BootScreen />
  }

  if (connection === 'exited') {
    return (
      <ExitScreen
        onRestart={() =>
          void (async () => {
            const cwd = lastCwd !== '' ? lastCwd : await defaultProjectCwd()
            await useAppStore.getState().connect(undefined, cwd)
          })()
        }
      />
    )
  }

  if (view === 'settings') return <SettingsPage />
  if (view === 'tools') return <ToolsPage />

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
      sidebar={<Sidebar onCollapse={() => setCollapsed(true)} />}
      rightbar={
        <RightPanel
          width={rightbarWidth}
          onWidth={(width) => {
            setRightbarWidth(width)
            persistWidth(RIGHTBAR_KEY, width)
          }}
          onClose={() => useWorkPanelStore.getState().closePanel()}
        />
      }
    >
      <ConversationTopbar
        collapsed={collapsed}
        onExpandSidebar={() => setCollapsed(false)}
        onNewSession={() => void useAppStore.getState().newSession()}
      />
      {switching !== null && (
        <div className={css.switchBanner} role="status">
          <div className={css.bootSpinner} aria-hidden />
          <span>{t('app.switching', { path: switching.path })}</span>
        </div>
      )}
      <NoticeBanner />
      <UpdateBanner />
      <ChatView />
      <div ref={dockRef} className={css.composerDock} data-shell-composer>
        <InputBar />
      </div>
      <button
        type="button"
        className={css.workPanelToggle}
        aria-label={t('panel.open')}
        aria-expanded={rightbarOpen}
        data-active={rightbarOpen || undefined}
        onClick={() => {
          const panel = useWorkPanelStore.getState()
          if (panel.open) panel.closePanel()
          else panel.openPanel()
        }}
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
  const startFailure = useAppStore((state) => state.startFailure)
  const tail = stderrTail.slice(-6).join('\n')
  if (isRuntimeMissing(startFailure) && startFailure !== null) {
    return <RuntimeScreen failure={startFailure} onRestart={onRestart} />
  }
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

/** The bundled runtime is gone (or not executable): say which binary, where it
 * was looked for, and offer the diagnostics instead of a raw spawn error. */
function RuntimeScreen({ failure, onRestart }: { failure: { bin: string; osError: string }; onRestart(): void }) {
  const t = useT()
  const [probe, setProbe] = useState<RuntimeProbe | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let live = true
    void invoke<RuntimeProbe>('runtime_probe')
      .then((result) => {
        if (live) setProbe(result)
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])

  const diagnostics = [
    `bin: ${failure.bin}`,
    `os: ${failure.osError}`,
    probe?.resolved != null ? `resolved: ${probe.resolved}` : 'resolved: (unknown)',
    probe !== null ? `exists: ${probe.exists}` : 'exists: (unknown)',
    probe?.version != null ? `version: ${probe.version}` : 'version: (unknown)',
  ].join('\n')

  const copy = () => {
    void navigator.clipboard
      .writeText(diagnostics)
      .then(() => setCopied(true))
      .catch(() => setCopied(false))
  }

  return (
    <div className={css.bootScreen}>
      <div className={css.exitCard}>
        <div className={css.exitTitle}>{t('connection.runtimeMissing')}</div>
        <pre className={css.exitTail}>{diagnostics}</pre>
        <div className={css.exitActions}>
          <button type="button" className={css.exitButton} onClick={copy}>
            {copied ? t('connection.copied') : t('connection.copyDiagnostics')}
          </button>
          <button type="button" className={css.exitButton} onClick={onRestart}>
            {t('connection.restart')}
          </button>
        </div>
      </div>
    </div>
  )
}
