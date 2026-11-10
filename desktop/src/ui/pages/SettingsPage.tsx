/** Full-window settings page with pi's two-pane anatomy: a searchable
 * destination rail pinned to the left edge, and a scrolling content column
 * whose rows are single-decision tiles. Every destination is backed by the
 * persisted settings store; there are no placeholder sections. */

import {
  Suspense,
  lazy,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import clsx from 'clsx'
import {
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  clampFontScale,
  useSettingsStore,
  type Locale,
  type ThemeId,
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
import { ArrowLeftIcon, CheckIcon, ChevronDownIcon, SearchIcon } from '../icons'
import { NAV_GROUPS, NAV_ITEMS, type Tab } from './settingsNav'
import { ZenWordmark } from '../ZenWordmark'
import css from './SettingsPage.module.css'

// Each destination is its own chunk: opening Settings must not fetch every
// provider, skill and project panel at once.
const ModelSettings = lazy(() =>
  import('./ModelSettings').then((module) => ({ default: module.ModelSettings })),
)
const SkillSettings = lazy(() =>
  import('./SkillSettings').then((module) => ({ default: module.SkillSettings })),
)
const LimitSettings = lazy(() =>
  import('./LimitSettings').then((module) => ({ default: module.LimitSettings })),
)
const McpSettings = lazy(() =>
  import('./McpSettings').then((module) => ({ default: module.McpSettings })),
)
const PluginSettings = lazy(() =>
  import('./PluginSettings').then((module) => ({ default: module.PluginSettings })),
)
const ProjectSettings = lazy(() =>
  import('./ProjectSettings').then((module) => ({ default: module.ProjectSettings })),
)

const THEMES: { value: ThemeMode; labelKey: string }[] = [
  { value: 'auto', labelKey: 'theme.auto' },
  { value: 'light', labelKey: 'theme.light' },
  { value: 'dark', labelKey: 'theme.dark' },
]

/** One preview triple per palette side; the picker shows the side that the
 * current mode setting resolves to, so cards read as they would render. */
interface PalettePreview {
  bg: string
  line: string
  lineSoft: string
  accent: string
  edge: string
}

const PALETTES: { value: ThemeId; labelKey: string; light: PalettePreview; dark: PalettePreview }[] = [  {
    value: 'zen',
    labelKey: 'theme.zen',
    light: { bg: '#ffffff', line: '#0f1115', lineSoft: '#adb2b8', accent: '#0f1115', edge: 'rgba(0,0,0,0.1)' },
    dark: { bg: '#151517', line: '#f9fafb', lineSoft: '#61656b', accent: '#f9fafb', edge: 'rgba(255,255,255,0.12)' },
  },
  {
    value: 'vscode',
    labelKey: 'theme.vscode',
    light: { bg: '#ffffff', line: '#3b3b3b', lineSoft: '#9d9d9d', accent: '#005fb8', edge: 'rgba(0,0,0,0.1)' },
    dark: { bg: '#1f1f1f', line: '#cccccc', lineSoft: '#6f6f6f', accent: '#0078d4', edge: 'rgba(255,255,255,0.12)' },
  },
  {
    value: 'github',
    labelKey: 'theme.github',
    light: { bg: '#ffffff', line: '#1f2328', lineSoft: '#818b98', accent: '#0969da', edge: 'rgba(0,0,0,0.1)' },
    dark: { bg: '#0d1117', line: '#e6edf3', lineSoft: '#6e7681', accent: '#1f6feb', edge: 'rgba(255,255,255,0.12)' },
  },
  {
    value: 'tokyonight',
    labelKey: 'theme.tokyonight',
    light: { bg: '#e1e2e7', line: '#3760bf', lineSoft: '#9aa2c6', accent: '#2e7de9', edge: 'rgba(54,89,169,0.24)' },
    dark: { bg: '#1a1b26', line: '#c0caf5', lineSoft: '#6b74a3', accent: '#7aa2f7', edge: 'rgba(169,177,214,0.2)' },
  },
]

function resolvesToDark(mode: ThemeMode, systemDark: boolean): boolean {
  return mode === 'dark' || (mode === 'auto' && systemDark)
}

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

function Row(props: {
  title: string
  detail?: string
  nested?: boolean
  /** Stacks the control under the copy so a wide control gets the full row. */
  wide?: boolean
  children: ReactNode
}) {
  return (
    <div className={clsx(css.row, props.nested && css.rowNested, props.wide && css.rowWide)}>
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

/** The palette chooser: four miniature canvases, each a two-line document and
 * an accent chip drawn in that palette's own colors. */
function PalettePicker(props: {
  value: ThemeId
  dark: boolean
  label: string
  onChange(value: ThemeId): void
}) {
  const t = useT()
  return (
    <div className={css.paletteGrid} role="group" aria-label={props.label}>
      {PALETTES.map((palette) => {
        const preview = props.dark ? palette.dark : palette.light
        return (
          <button
            key={palette.value}
            type="button"
            className={css.paletteItem}
            aria-pressed={palette.value === props.value}
            onClick={() => props.onChange(palette.value)}
          >
            <span
              className={css.palettePreview}
              style={{ background: preview.bg, boxShadow: `inset 0 0 0 1px ${preview.edge}` }}
            >
              <span className={css.paletteLines}>
                <span
                  className={css.paletteLine}
                  style={{ width: '62%', background: preview.line }}
                />
                <span
                  className={css.paletteLine}
                  style={{ width: '40%', background: preview.lineSoft }}
                />
              </span>
              <span className={css.paletteChip} style={{ background: preview.accent }} />
            </span>
            <span className={css.paletteName}>{t(palette.labelKey)}</span>
          </button>
        )
      })}
    </div>
  )
}

/** Manual check: ignores the cache and says what happened, unlike the silent
 * boot check. Install still goes through the sidebar notification. */
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
  const themeId = useSettingsStore((state) => state.themeId)
  const locale = useSettingsStore((state) => state.locale)
  const fontScale = useSettingsStore((state) => state.fontScale)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  const sessionTime = useSettingsStore((state) => state.sessionTime)
  const updateChecks = useSettingsStore((state) => state.updateChecks)
  const showThinking = useSettingsStore((state) => state.showThinking)
  const autoCollapseThinking = useSettingsStore((state) => state.autoCollapseThinking)
  const contextUsageDisplay = useSettingsStore((state) => state.contextUsageDisplay)
  const enterToSend = useSettingsStore((state) => state.enterToSend)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const setThemeId = useSettingsStore((state) => state.setThemeId)
  const setLocale = useSettingsStore((state) => state.setLocale)
  const setFontScale = useSettingsStore((state) => state.setFontScale)
  const setChatWidth = useSettingsStore((state) => state.setChatWidth)
  const setSessionTime = useSettingsStore((state) => state.setSessionTime)
  const setUpdateChecks = useSettingsStore((state) => state.setUpdateChecks)
  const setShowThinking = useSettingsStore((state) => state.setShowThinking)
  const setAutoCollapseThinking = useSettingsStore((state) => state.setAutoCollapseThinking)
  const setContextUsageDisplay = useSettingsStore((state) => state.setContextUsageDisplay)
  const setEnterToSend = useSettingsStore((state) => state.setEnterToSend)
  const setView = useSettingsStore((state) => state.setView)

  const [tab, setTab] = useState<Tab>('appearance')
  const [query, setQuery] = useState('')
  // The palette previews follow the resolved side, so track the system half
  // of 'auto' here too.
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystemDark(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
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
                <Row wide title={t('settings.themePalette')} detail={t('settings.themePaletteHint')}>
                  <PalettePicker
                    value={themeId}
                    dark={resolvesToDark(theme, systemDark)}
                    label={t('settings.themePalette')}
                    onChange={setThemeId}
                  />
                </Row>
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

          {tab === 'models' && (
            <Suspense fallback={null}>
              <ModelSettings />
            </Suspense>
          )}

          {tab === 'skills' && (
            <Suspense fallback={null}>
              <SkillSettings />
            </Suspense>
          )}

          {tab === 'limits' && (
            <Suspense fallback={null}>
              <LimitSettings />
            </Suspense>
          )}

          {tab === 'mcp' && (
            <Suspense fallback={null}>
              <McpSettings />
            </Suspense>
          )}

          {tab === 'plugins' && (
            <Suspense fallback={null}>
              <PluginSettings />
            </Suspense>
          )}

          {tab === 'projects' && (
            <Suspense fallback={null}>
              <ProjectSettings />
            </Suspense>
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
                <Row title={t('settings.showThinking')} detail={t('settings.showThinkingHint')}>
                  <Switch
                    checked={showThinking}
                    label={t('settings.showThinking')}
                    onChange={setShowThinking}
                  />
                </Row>
                {showThinking && (
                  <Row
                    nested
                    title={t('settings.autoCollapseThinking')}
                    detail={t('settings.autoCollapseThinkingHint')}
                  >
                    <Switch
                      checked={autoCollapseThinking}
                      label={t('settings.autoCollapseThinking')}
                      onChange={setAutoCollapseThinking}
                    />
                  </Row>
                )}
                <Row
                  title={t('settings.contextUsageDisplay')}
                  detail={t('settings.contextUsageDisplayHint')}
                >
                  <Segmented
                    value={contextUsageDisplay}
                    label={t('settings.contextUsageDisplay')}
                    options={[
                      {
                        value: 'remaining',
                        label: t('settings.contextUsageDisplayRemaining'),
                      },
                      { value: 'used', label: t('settings.contextUsageDisplayUsed') },
                    ]}
                    onChange={setContextUsageDisplay}
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
              <Card title={t('settings.chatInput')}>
                <Row title={t('settings.enterToSend')} detail={t('settings.enterToSendHint')}>
                  <Switch
                    checked={enterToSend}
                    label={t('settings.enterToSend')}
                    onChange={setEnterToSend}
                  />
                </Row>
              </Card>
            </div>
          )}

          {tab === 'about' && (
            <div className={css.aboutHero}>
              <ZenWordmark height={28} />
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
