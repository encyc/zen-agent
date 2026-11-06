/** Full-window settings page with pi's two-pane anatomy: a searchable
 * destination rail pinned to the left edge, and a scrolling content column
 * whose rows are single-decision tiles. Every destination is backed by the
 * persisted settings store; there are no placeholder sections. */

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  clampFontScale,
  useSettingsStore,
  type Locale,
  type ThemeMode,
} from '../../state/stores'
import { useT } from '../../i18n/locale'
import { useUpdateStore } from '../../state/updateStore'
import {
  CHAT_WIDTH_DEFAULT,
  CHAT_WIDTH_MAX,
  CHAT_WIDTH_MIN,
  clampChatWidth,
} from '../columns'
import {
  ArrowLeftIcon,
  ChatIcon,
  CheckIcon,
  ChevronDownIcon,
  InfoIcon,
  PaletteIcon,
  SearchIcon,
} from '../icons'
import css from './SettingsPage.module.css'

type Tab = 'appearance' | 'conversation' | 'about'
type NavGroup = 'preferences' | 'system'

interface NavItem {
  id: Tab
  labelKey: string
  hintKey: string
  icon: ReactNode
  group: NavGroup
  keywords: string[]
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'appearance',
    labelKey: 'settings.appearance',
    hintKey: 'settings.appearanceHint',
    icon: <PaletteIcon size={14} />,
    group: 'preferences',
    keywords: ['settings.theme', 'settings.language', 'settings.fontScale'],
  },
  {
    id: 'conversation',
    labelKey: 'settings.conversation',
    hintKey: 'settings.conversationHint',
    icon: <ChatIcon size={14} />,
    group: 'preferences',
    keywords: ['settings.sessionTime', 'settings.chatWidth'],
  },
  {
    id: 'about',
    labelKey: 'settings.about',
    hintKey: 'settings.aboutHint',
    icon: <InfoIcon size={14} />,
    group: 'system',
    keywords: ['settings.aboutVersion'],
  },
]

const NAV_GROUPS: { id: NavGroup; labelKey: string }[] = [
  { id: 'preferences', labelKey: 'settings.groupPreferences' },
  { id: 'system', labelKey: 'settings.groupSystem' },
]

const THEMES: { value: ThemeMode; labelKey: string }[] = [
  { value: 'auto', labelKey: 'theme.auto' },
  { value: 'light', labelKey: 'theme.light' },
  { value: 'dark', labelKey: 'theme.dark' },
]

const LOCALES: { value: Locale; labelKey: string }[] = [
  { value: 'zh', labelKey: 'language.zh' },
  { value: 'en', labelKey: 'language.en' },
]

const FONT_PRESETS = [
  { scale: FONT_SCALE_MIN, labelKey: 'settings.fontSizeSmall' },
  { scale: 1, labelKey: 'settings.fontSizeDefault' },
  { scale: 1.1, labelKey: 'settings.fontSizeLarge' },
  { scale: FONT_SCALE_MAX, labelKey: 'settings.fontSizeXl' },
]

function Row(props: { title: string; detail?: string; children: ReactNode }) {
  return (
    <div className={css.row}>
      <div className={css.rowCopy}>
        <div className={css.rowTitle}>{props.title}</div>
        {props.detail ? <div className={css.rowDetail}>{props.detail}</div> : null}
      </div>
      <div className={css.rowControl}>{props.children}</div>
    </div>
  )
}

function Card(props: { title: string; caption?: string; children: ReactNode }) {
  return (
    <section className={css.cardBlock}>
      <div className={css.cardHeadline}>
        <h2 className={css.cardHeading}>{props.title}</h2>
        {props.caption ? <p className={css.cardCaption}>{props.caption}</p> : null}
      </div>
      <div className={css.panel}>{props.children}</div>
    </section>
  )
}

function Picker<T extends string>(props: {
  value: T
  label: string
  options: { value: T; label: string }[]
  onChange(value: T): void
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement | null>(null)
  const selected = props.options.find((option) => option.value === props.value) ?? props.options[0]

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node)) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className={css.picker} ref={root}>
      <button
        type="button"
        className={css.pickerTrigger}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={props.label}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={css.pickerLabel}>{selected?.label}</span>
        <ChevronDownIcon size={14} />
      </button>
      {open && (
        <div className={css.pickerMenu} role="listbox" aria-label={props.label}>
          {props.options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === props.value}
              className={css.pickerOption}
              onClick={() => {
                setOpen(false)
                props.onChange(option.value)
              }}
            >
              <span>{option.label}</span>
              {option.value === props.value && <CheckIcon size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Segmented<T extends string>(props: {
  value: T
  label: string
  options: { value: T; label: string }[]
  onChange(value: T): void
}) {
  return (
    <div className={css.segment} role="group" aria-label={props.label}>
      {props.options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={css.segmentItem}
          aria-pressed={option.value === props.value}
          onClick={() => props.onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** Manual check: ignores the cache and says what happened, unlike the silent
 * boot check. Install still goes through the banner. */
function UpdateCheckButton() {
  const t = useT()
  const [result, setResult] = useState<'latest' | 'available' | 'failed' | null>(null)
  const [running, setRunning] = useState(false)
  return (
    <div className={css.aboutCheck}>
      <button
        type="button"
        className={css.reset}
        disabled={running}
        onClick={() => {
          setRunning(true)
          setResult(null)
          void useUpdateStore
            .getState()
            .checkNow()
            .then((outcome) => setResult(outcome))
            .finally(() => setRunning(false))
        }}
      >
        {running ? t('update.checking') : t('update.check')}
      </button>
      {result !== null && (
        <span className={css.value}>
          {result === 'latest'
            ? t('update.latest')
            : result === 'available'
              ? t('update.available', { version: useUpdateStore.getState().cache?.latest ?? '' })
              : t('update.checkFailed')}
        </span>
      )}
    </div>
  )
}

function Switch(props: { checked: boolean; label: string; onChange(checked: boolean): void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={props.checked}
      aria-label={props.label}
      className={css.switch}
      onClick={() => props.onChange(!props.checked)}
    >
      <span className={css.switchThumb} />
    </button>
  )
}

export function SettingsPage() {
  const t = useT()
  const theme = useSettingsStore((state) => state.theme)
  const locale = useSettingsStore((state) => state.locale)
  const fontScale = useSettingsStore((state) => state.fontScale)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  const sessionTime = useSettingsStore((state) => state.sessionTime)
  const updateChecks = useSettingsStore((state) => state.updateChecks)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const setLocale = useSettingsStore((state) => state.setLocale)
  const setFontScale = useSettingsStore((state) => state.setFontScale)
  const setChatWidth = useSettingsStore((state) => state.setChatWidth)
  const setSessionTime = useSettingsStore((state) => state.setSessionTime)
  const setUpdateChecks = useSettingsStore((state) => state.setUpdateChecks)
  const setView = useSettingsStore((state) => state.setView)

  const [tab, setTab] = useState<Tab>('appearance')
  const [query, setQuery] = useState('')
  const contentRef = useRef<HTMLElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const active = NAV_ITEMS.find((item) => item.id === tab) ?? NAV_ITEMS[0]

  // pi focuses the rail search without letting it scroll the content column,
  // and resets that column before paint whenever the destination changes.
  useLayoutEffect(() => {
    searchRef.current?.focus({ preventScroll: true })
  }, [])
  useLayoutEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0
  }, [tab])

  const needle = query.trim().toLowerCase()
  const groups = useMemo(
    () =>
      NAV_GROUPS.map((group) => ({
        ...group,
        items: NAV_ITEMS.filter(
          (item) =>
            item.group === group.id &&
            (needle === '' ||
              [t(item.labelKey), ...item.keywords.map((key) => t(key))].some((text) =>
                text.toLowerCase().includes(needle),
              )),
        ),
      })).filter((group) => group.items.length > 0),
    // t is recreated per render; the query and locale drive the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [needle, locale],
  )

  return (
    <div className={css.shell} data-page="settings">
      <div className={css.titlebar} aria-hidden data-tauri-drag-region />

      <aside className={css.nav} aria-label={t('settings.title')}>
        <div className={css.navTop} data-tauri-drag-region>
          <div className={css.searchWrap}>
            <SearchIcon size={14} />
            <input
              ref={searchRef}
              className={css.search}
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('settings.searchPlaceholder')}
              aria-label={t('settings.search')}
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="off"
            />
          </div>
        </div>

        <nav className={css.navScroll}>
          {groups.length === 0 ? (
            <div className={css.navEmpty}>{t('settings.noResults')}</div>
          ) : (
            groups.map((group) => (
              <div key={group.id} className={css.navGroup}>
                <div className={css.navGroupLabel}>{t(group.labelKey)}</div>
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={css.navItem}
                    data-active={item.id === tab || undefined}
                    onClick={() => setTab(item.id)}
                  >
                    <span className={css.navIcon}>{item.icon}</span>
                    <span className={css.navLabel}>{t(item.labelKey)}</span>
                  </button>
                ))}
              </div>
            ))
          )}
        </nav>

        <div className={css.navFooter}>
          <button
            type="button"
            className={css.back}
            aria-label={t('common.back')}
            onClick={() => setView('chat')}
          >
            <ArrowLeftIcon size={15} />
            <span>{t('settings.backToApp')}</span>
          </button>
        </div>
      </aside>

      <main className={css.content} ref={contentRef}>
        <div className={`${css.contentInner} ${css.contentEnter}`}>
          <header className={css.pageHeader}>
            <span className={css.pageIcon} aria-hidden>
              {active.icon}
            </span>
            <div className={css.pageHeading}>
              <h1 className={css.sectionTitle}>{t(active.labelKey)}</h1>
              <p className={css.pageSubtitle}>{t(active.hintKey)}</p>
            </div>
          </header>

          {tab === 'appearance' && (
            <div className={css.stack}>
              <Card title={t('settings.interface')}>
                <Row title={t('settings.theme')} detail={t('settings.themeHint')}>
                  <Picker
                    value={theme}
                    label={t('settings.theme')}
                    options={THEMES.map((option) => ({
                      value: option.value,
                      label: t(option.labelKey),
                    }))}
                    onChange={setTheme}
                  />
                </Row>
                <Row title={t('settings.language')} detail={t('settings.languageHint')}>
                  <Picker
                    value={locale}
                    label={t('settings.language')}
                    options={LOCALES.map((option) => ({
                      value: option.value,
                      label: t(option.labelKey),
                    }))}
                    onChange={setLocale}
                  />
                </Row>
                <Row title={t('settings.fontScale')} detail={t('settings.fontScaleHint')}>
                  <div className={css.fontSize}>
                    <Segmented
                      value={String(clampFontScale(fontScale))}
                      label={t('settings.fontScale')}
                      options={FONT_PRESETS.map((preset) => ({
                        value: String(preset.scale),
                        label: t(preset.labelKey),
                      }))}
                      onChange={(value) => setFontScale(Number(value))}
                    />
                    <div className={css.sliderRow}>
                      <input
                        type="range"
                        min={FONT_SCALE_MIN}
                        max={FONT_SCALE_MAX}
                        step={0.05}
                        value={clampFontScale(fontScale)}
                        aria-label={t('settings.fontSizeScale')}
                        onChange={(event) => setFontScale(Number(event.target.value))}
                      />
                      <span className={css.value}>{Math.round(fontScale * 100)}%</span>
                    </div>
                  </div>
                </Row>
              </Card>
            </div>
          )}

          {tab === 'conversation' && (
            <div className={css.stack}>
              <Card
                title={t('settings.chatDisplay')}
                caption={t('settings.chatDisplayCaption')}
              >
                <Row title={t('settings.sessionTime')} detail={t('settings.sessionTimeHint')}>
                  <Switch
                    checked={sessionTime}
                    label={t('settings.sessionTime')}
                    onChange={setSessionTime}
                  />
                </Row>
                {__UPDATE_ENABLED__ && (
                  <Row title={t('update.toggle')} detail={t('update.toggleHint')}>
                    <Switch
                      checked={updateChecks}
                      label={t('update.toggle')}
                      onChange={setUpdateChecks}
                    />
                  </Row>
                )}
                <Row title={t('settings.chatWidth')} detail={t('settings.chatWidthHint')}>
                  <div className={css.widthControl}>
                    <div className={css.sliderRow}>
                      <input
                        type="range"
                        min={CHAT_WIDTH_MIN}
                        max={CHAT_WIDTH_MAX}
                        step={10}
                        value={clampChatWidth(chatWidth)}
                        aria-label={t('settings.chatWidth')}
                        onChange={(event) => setChatWidth(clampChatWidth(Number(event.target.value)))}
                      />
                      <span className={css.value}>{clampChatWidth(chatWidth)}px</span>
                    </div>
                    <button
                      type="button"
                      className={css.reset}
                      onClick={() => setChatWidth(CHAT_WIDTH_DEFAULT)}
                    >
                      {t('settings.reset')}
                    </button>
                  </div>
                </Row>
              </Card>
            </div>
          )}

          {tab === 'about' && (
            <div className={css.aboutHero}>
              <div className={css.aboutName}>ZenAgent</div>
              <div className={css.aboutVersion}>
                {t('settings.aboutVersion', { version: __APP_VERSION__ })}
              </div>
              {__UPDATE_ENABLED__ && <UpdateCheckButton />}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
