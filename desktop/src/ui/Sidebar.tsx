/** The sidebar shell: a brand header, the workspace mode switcher, the body
 * for the active mode, and the footer. The coding body lives in SidebarCoding;
 * zen and swarm bodies are dispatched here as they land. */

import { useSettingsStore } from '../state/stores'
import { useT } from '../i18n/locale'
import { Tooltip } from './primitives'
import {
  MonitorIcon,
  MoonIcon,
  PanelLeftIcon,
  SettingsIcon,
  SunIcon,
} from './icons'
import { Notifications } from './Notifications'
import { ModeSwitcher } from './ModeSwitcher'
import { captureChatState } from './pages/navigation'
import { AppMark } from './AppMark'
import { ZenWordmark } from './ZenWordmark'
import { SidebarCoding } from './SidebarCoding'
import { ZenSidebar } from './ZenSidebar'
import css from './Sidebar.module.css'

export function Sidebar(props: { onCollapse(): void }) {
  const workspaceMode = useSettingsStore((state) => state.workspaceMode)

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

      <ModeSwitcher />

      {workspaceMode === 'zen' ? <ZenSidebar /> : workspaceMode === 'swarm' ? <SidebarCoding /> : <SidebarCoding />}

      <Footer />
    </div>
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