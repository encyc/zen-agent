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
  footer,
  children,
  onClose,
  data,
}: {
  query?: string
  onQuery?(value: string): void
  /** Pinned below the scrolling list; stays reachable without scrolling. */
  footer?: React.ReactNode
  children: React.ReactNode
  onClose(): void
  data?: Record<string, string>
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
    <div className={css.menu} ref={ref} role="menu" {...(data as unknown as React.HTMLAttributes<HTMLDivElement>)}>
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
      {footer !== undefined && <div className={css.footer}>{footer}</div>}
    </div>
  )
}

const THINKING_LEVELS = ['off', 'low', 'medium', 'high', 'max']

export function ModelMenu({
  thinking,
  onThinkingPick,
  onClose,
}: {
  thinking: string | null
  onThinkingPick(level: string): void
  onClose(): void
}) {
  const t = useT()
  const models = useCatalogStore((state) => state.models)
  const setModel = useAppStore((state) => state.setModel)
  const [query, setQuery] = useState('')
  const level = thinking ?? 'off'
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
    <MenuShell
      query={query}
      onQuery={setQuery}
      onClose={onClose}
      footer={
        <div className={css.section}>
          <div className={css.groupLabel}>{t('menu.thinking')}</div>
          <div className={css.segments} role="radiogroup" aria-label={t('menu.thinking')}>
            {THINKING_LEVELS.map((candidate) => (
              <button
                key={candidate}
                type="button"
                role="radio"
                aria-checked={level === candidate}
                data-active={level === candidate || undefined}
                className={css.segment}
                onClick={() => onThinkingPick(candidate)}
              >
                {candidate}
              </button>
            ))}
          </div>
        </div>
      }
    >
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

export function RoleModelMenu({
  current,
  mixed = false,
  onPick,
  onClose,
}: {
  /** The model all targets share; null for "role default" or mixed targets. */
  current: string | null
  /** Targets disagree, so no row can be checked. */
  mixed?: boolean
  onPick(modelId: string | null): void
  onClose(): void
}) {
  const t = useT()
  const models = useCatalogStore((state) => state.models)
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
  const checked = (id: string | null) => !mixed && current === id
  const custom = current !== null && !models.some((model) => model.id === current)

  return (
    <MenuShell
      query={query}
      onQuery={setQuery}
      onClose={onClose}
      data={{ 'data-role-model-menu': '' }}
    >
      <div className={css.groupLabel}>{t('menu.model')}</div>
      <button
        type="button"
        className={css.item}
        data-model=""
        onClick={() => onPick(null)}
      >
        <span className={css.itemTitle}>{t('swarm.modelDefault')}</span>
        <span className={css.itemSub}>{t('swarm.modelDefaultHint')}</span>
        {checked(null) && <CheckIcon size={12} />}
      </button>
      {custom && (
        <button
          type="button"
          className={css.item}
          data-model={current}
          onClick={() => onPick(current)}
        >
          <span className={css.itemTitle}>{current}</span>
          {checked(current) && <CheckIcon size={12} />}
        </button>
      )}
      {filtered.available.map((model) => (
        <button
          key={`${model.provider}/${model.id}`}
          type="button"
          className={css.item}
          data-model={model.id}
          onClick={() => onPick(model.id)}
        >
          <span className={css.itemTitle}>{model.id}</span>
          <span className={css.itemSub}>
            {model.provider} · {Math.round(model.context_window / 1000)}k
            {model.reasoning ? ' · reasoning' : ''}
            {model.supports_vision ? ' · vision' : ''}
          </span>
          {checked(model.id) && <CheckIcon size={12} />}
        </button>
      ))}
      {filtered.unavailable.length > 0 && <div className={css.groupLabel}>no key</div>}
      {filtered.unavailable.map((model) => (
        <button
          key={`${model.provider}/${model.id}`}
          type="button"
          className={css.item}
          data-model={model.id}
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
