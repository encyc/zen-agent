/** The sidebar shell: a brand header, the conversation body, and the footer. */

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
import { captureChatState } from './pages/navigation'
import { AppMark } from './AppMark'
import { ZenWordmark } from './ZenWordmark'
import { SidebarCoding } from './SidebarCoding'
import css from './Sidebar.module.css'

export function Sidebar(props: { onCollapse(): void }) {
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

      <SidebarCoding />

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