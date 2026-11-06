/** Floating composer menus: model catalog, mode picker, thinking level —
 * bottom-anchored like dsh's composer menus, with grouped models. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useCatalogStore, useSettingsStore, type ThemeMode } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { CheckIcon, MonitorIcon, MoonIcon, SearchIcon, SunIcon } from '../icons'
import css from './Menus.module.css'

function MenuShell({
  query,
  onQuery,
  children,
  onClose,
}: {
  query?: string
  onQuery?(value: string): void
  children: React.ReactNode
  onClose(): void
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current !== null && !ref.current.contains(event.target as Node)) onClose()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])
  return (
    <div className={css.menu} ref={ref} role="menu">
      {onQuery !== undefined && (
        <div className={css.searchRow}>
          <SearchIcon size={12} />
          <input
            className={css.search}
            value={query ?? ''}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="filter…"
            spellCheck={false}
            autoFocus
          />
        </div>
      )}
      <div className={css.list}>{children}</div>
    </div>
  )
}

export function ModelMenu({ onClose }: { onClose(): void }) {
  const t = useT()
  const models = useCatalogStore((state) => state.models)
  const setModel = useAppStore((state) => state.setModel)
  const [query, setQuery] = useState('')
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = models.filter(
      (model) => q === '' || model.id.toLowerCase().includes(q) || model.provider.toLowerCase().includes(q),
    )
    const available = list.filter((model) => model.available)
    const unavailable = list.filter((model) => !model.available)
    return { available, unavailable: unavailable.slice(0, 12) }
  }, [models, query])

  return (
    <MenuShell query={query} onQuery={setQuery} onClose={onClose}>
      <div className={css.groupLabel}>{t('menu.model')}</div>
      {filtered.available.map((model) => (
        <button
          key={`${model.provider}/${model.id}`}
          type="button"
          className={css.item}
          onClick={() => {
            void setModel(model.id)
            onClose()
          }}
        >
          <span className={css.itemTitle}>{model.id}</span>
          <span className={css.itemSub}>
            {model.provider} · {Math.round(model.context_window / 1000)}k
            {model.reasoning ? ' · reasoning' : ''}
            {model.supports_vision ? ' · vision' : ''}
          </span>
          {model.current && <CheckIcon size={12} />}
        </button>
      ))}
      {filtered.unavailable.length > 0 && (
        <div className={css.groupLabel}>no key</div>
      )}
      {filtered.unavailable.map((model) => (
        <button
          key={`${model.provider}/${model.id}`}
          type="button"
          className={css.item}
          disabled
          title={model.reason ?? undefined}
        >
          <span className={css.itemTitle}>{model.id}</span>
          <span className={css.itemSub}>{model.provider}</span>
        </button>
      ))}
    </MenuShell>
  )
}

const MODES: { id: 'plan' | 'ask' | 'zen'; key: string }[] = [
  { id: 'plan', key: 'mode.plan' },
  { id: 'ask', key: 'mode.ask' },
  { id: 'zen', key: 'mode.zen' },
]

export function ModeMenu({
  mode,
  onPick,
  onClose,
}: {
  mode: string
  onPick(mode: 'plan' | 'ask' | 'zen'): void
  onClose(): void
}) {
  const t = useT()
  return (
    <MenuShell onClose={onClose}>
      <div className={css.groupLabel}>{t('menu.mode')}</div>
      {MODES.map((entry) => (
        <button
          key={entry.id}
          type="button"
          className={css.item}
          onClick={() => onPick(entry.id)}
        >
          <span className={css.itemTitle}>{t(`status.mode.${entry.id}`)}</span>
          <span className={css.itemSub}>{t(entry.key)}</span>
          {mode === entry.id && <CheckIcon size={12} />}
        </button>
      ))}
    </MenuShell>
  )
}

const THINKING_LEVELS = ['off', 'low', 'medium', 'high', 'max']

export function ThinkingMenu({
  thinking,
  onPick,
  onClose,
}: {
  thinking: string | null
  onPick(level: string): void
  onClose(): void
}) {
  const t = useT()
  return (
    <MenuShell onClose={onClose}>
      <div className={css.groupLabel}>{t('menu.thinking')}</div>
      {THINKING_LEVELS.map((level) => (
        <button
          key={level}
          type="button"
          className={css.item}
          onClick={() => onPick(level)}
        >
          <span className={css.itemTitle}>{level}</span>
          {(thinking ?? 'off') === level && <CheckIcon size={12} />}
        </button>
      ))}
    </MenuShell>
  )
}

export function ThemeMenu({ onClose }: { onClose(): void }) {
  const t = useT()
  const theme = useSettingsStore((state) => state.theme)
  const setTheme = useSettingsStore((state) => state.setTheme)
  const options: { id: ThemeMode; label: string; icon: React.ReactNode }[] = [
    { id: 'light', label: t('theme.light'), icon: <SunIcon size={13} /> },
    { id: 'dark', label: t('theme.dark'), icon: <MoonIcon size={13} /> },
    { id: 'auto', label: t('theme.auto'), icon: <MonitorIcon size={13} /> },
  ]
  return (
    <MenuShell onClose={onClose}>
      <div className={css.groupLabel}>{t('menu.theme')}</div>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className={css.item}
          onClick={() => {
            setTheme(option.id)
            onClose()
          }}
        >
          <span className={css.itemIcon}>{option.icon}</span>
          <span className={css.itemTitle}>{option.label}</span>
          {theme === option.id && <CheckIcon size={12} />}
        </button>
      ))}
    </MenuShell>
  )
}
