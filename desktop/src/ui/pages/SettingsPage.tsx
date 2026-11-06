/** Local-only settings surface: appearance, language, session time, font
 * scale and the conversation band width. Every control writes the persisted
 * settings store; the shell reads them back as CSS properties. */

import {
  clampFontScale,
  useSettingsStore,
  type Locale,
  type ThemeMode,
} from '../../state/stores'
import { useT } from '../../i18n/locale'
import {
  CHAT_WIDTH_DEFAULT,
  CHAT_WIDTH_MAX,
  CHAT_WIDTH_MIN,
  clampChatWidth,
} from '../columns'
import { ArrowLeftIcon } from '../icons'
import css from './SettingsPage.module.css'

const THEMES: ThemeMode[] = ['auto', 'light', 'dark']
const LOCALES: Locale[] = ['zh', 'en']

export function SettingsPage() {
  const t = useT()
  const theme = useSettingsStore((state) => state.theme)
  const locale = useSettingsStore((state) => state.locale)
  const fontScale = useSettingsStore((state) => state.fontScale)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  const sessionTime = useSettingsStore((state) => state.sessionTime)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const setLocale = useSettingsStore((state) => state.setLocale)
  const setFontScale = useSettingsStore((state) => state.setFontScale)
  const setChatWidth = useSettingsStore((state) => state.setChatWidth)
  const setSessionTime = useSettingsStore((state) => state.setSessionTime)
  const setView = useSettingsStore((state) => state.setView)

  return (
    <div className={css.page} data-page="settings">
      <header className={css.header}>
        <button
          type="button"
          className={css.back}
          aria-label={t('common.back')}
          onClick={() => setView('chat')}
          autoFocus
        >
          <ArrowLeftIcon size={15} />
          <span>{t('common.back')}</span>
        </button>
        <h1 className={css.title}>{t('settings.title')}</h1>
        <span className={css.version}>v{__APP_VERSION__}</span>
      </header>

      <div className={css.body}>
        <section className={css.section} aria-labelledby="settings-appearance">
          <h2 id="settings-appearance" className={css.sectionTitle}>
            {t('settings.appearance')}
          </h2>
          <div className={css.segmented} role="group" aria-label={t('settings.appearance')}>
            {THEMES.map((mode) => (
              <button
                key={mode}
                type="button"
                className={css.segment}
                aria-pressed={theme === mode}
                onClick={() => setTheme(mode)}
              >
                {t(`theme.${mode}`)}
              </button>
            ))}
          </div>
        </section>

        <section className={css.section} aria-labelledby="settings-language">
          <h2 id="settings-language" className={css.sectionTitle}>
            {t('settings.language')}
          </h2>
          <div className={css.segmented} role="group" aria-label={t('settings.language')}>
            {LOCALES.map((value) => (
              <button
                key={value}
                type="button"
                className={css.segment}
                aria-pressed={locale === value}
                onClick={() => setLocale(value)}
              >
                {t(`language.${value}`)}
              </button>
            ))}
          </div>
        </section>

        <section className={css.section}>
          <div className={css.switchRow}>
            <label htmlFor="settings-session-time" className={css.label}>
              {t('settings.sessionTime')}
            </label>
            <input
              id="settings-session-time"
              type="checkbox"
              checked={sessionTime}
              onChange={(event) => setSessionTime(event.target.checked)}
            />
          </div>
          <p className={css.hint}>{t('settings.sessionTimeHint')}</p>
        </section>

        <section className={css.section}>
          <div className={css.labelRow}>
            <span className={css.label}>{t('settings.fontScale')}</span>
            <span className={css.value}>{Math.round(fontScale * 100)}%</span>
          </div>
          <input
            className={css.range}
            type="range"
            min={0.9}
            max={1.2}
            step={0.05}
            value={fontScale}
            aria-label={t('settings.fontScale')}
            onChange={(event) => setFontScale(clampFontScale(Number(event.target.value)))}
          />
        </section>

        <section className={css.section}>
          <div className={css.labelRow}>
            <span className={css.label}>{t('settings.chatWidth')}</span>
            <span className={css.value}>{clampChatWidth(chatWidth)}px</span>
          </div>
          <input
            className={css.range}
            type="range"
            min={CHAT_WIDTH_MIN}
            max={CHAT_WIDTH_MAX}
            step={10}
            value={clampChatWidth(chatWidth)}
            aria-label={t('settings.chatWidth')}
            onChange={(event) => setChatWidth(clampChatWidth(Number(event.target.value)))}
          />
          <button
            type="button"
            className={css.reset}
            onClick={() => setChatWidth(CHAT_WIDTH_DEFAULT)}
          >
            {t('settings.reset')}
          </button>
        </section>
      </div>
    </div>
  )
}
