/** ⌘K overlay: fuzzy-free substring search over the loaded session list and
 * the allowlisted slash commands. Resuming a session from another project
 * switches the runtime there first via the app store. */

import { useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import { useAppStore } from '../../state/appStore'
import { useCatalogStore, useComposerStore, usePaletteStore, useSessionStore } from '../../state/stores'
import { nextPaletteIndex, paletteResults, type PaletteCommandHit, type PaletteSessionHit } from '../../state/palette'
import { nextDraft } from '../../state/pending'
import { useT } from '../../i18n/locale'
import { SearchIcon } from '../icons'
import css from './CommandPalette.module.css'

type Option =
  | { kind: 'session'; hit: PaletteSessionHit; disabled: boolean }
  | { kind: 'command'; hit: PaletteCommandHit; disabled: boolean }

export function CommandPalette() {
  const t = useT()
  const open = usePaletteStore((state) => state.open)
  const query = usePaletteStore((state) => state.query)
  const setQuery = usePaletteStore((state) => state.setQuery)
  const close = usePaletteStore((state) => state.closePalette)
  const sessions = useSessionStore((state) => state.sessions)
  const currentPath = useSessionStore((state) => state.currentPath)
  const commands = useCatalogStore((state) => state.commands)
  const openSession = useAppStore((state) => state.openSession)
  const runCommand = useAppStore((state) => state.runCommand)
  const [active, setActive] = useState(-1)

  const results = useMemo(
    () => paletteResults(sessions, commands, query, currentPath),
    [sessions, commands, query, currentPath],
  )
  const flat = useMemo<Option[]>(
    () => [
      ...results.sessions.map((hit): Option => ({ kind: 'session', hit, disabled: hit.disabled })),
      ...results.commands.map((hit): Option => ({ kind: 'command', hit, disabled: false })),
    ],
    [results],
  )

  useEffect(() => {
    if (open) setActive(nextPaletteIndex(flat, -1, 1))
  }, [open, flat])

  if (!open) return null

  const activate = (option: Option) => {
    if (option.kind === 'session') {
      if (option.disabled) return
      close()
      if (option.hit.session.path !== currentPath) void openSession(option.hit.session.path)
      return
    }
    close()
    if (option.hit.mode === 'run') {
      void runCommand(option.hit.name, '')
      return
    }
    const composer = useComposerStore.getState()
    composer.setDraft(nextDraft(composer.draft, `/${option.hit.name} `))
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>('textarea')?.focus()
    })
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      close()
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((from) => nextPaletteIndex(flat, from, event.key === 'ArrowDown' ? 1 : -1))
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      const index = active === -1 ? nextPaletteIndex(flat, -1, 1) : active
      if (index >= 0) activate(flat[index])
    }
  }

  const optionIndex = (kind: 'session' | 'command', index: number): number =>
    kind === 'session' ? index : results.sessions.length + index

  const renderOption = (option: Option, index: number) => {
    if (option.kind === 'session') {
      const { session, disabled } = option.hit
      return (
        <button
          key={`session:${session.path}`}
          type="button"
          role="option"
          className={css.option}
          aria-selected={index === active}
          aria-disabled={disabled || undefined}
          data-active={index === active || undefined}
          data-disabled={disabled || undefined}
          onClick={() => activate(option)}
        >
          <span className={css.optionBody}>
            <span className={css.optionTitle}>{session.title || session.name}</span>
            <span className={css.optionSub}>
              {[session.project, session.model].filter((part) => part !== undefined && part !== '').join(' · ')}
            </span>
            {disabled && <span className={css.optionHint}>{t('palette.blockedSession')}</span>}
          </span>
        </button>
      )
    }
    return (
      <button
        key={`command:${option.hit.name}`}
        type="button"
        role="option"
        className={css.option}
        aria-selected={index === active}
        data-active={index === active || undefined}
        onClick={() => activate(option)}
      >
        <span className={css.optionBody}>
          <span className={css.optionTitle}>/{option.hit.name}</span>
          {option.hit.description !== '' && (
            <span className={css.optionSub}>{option.hit.description}</span>
          )}
        </span>
      </button>
    )
  }

  return (
    <div
      className={css.backdrop}
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        className={css.panel}
        role="dialog"
        aria-label={t('palette.title')}
        onKeyDown={onKeyDown}
      >
        <div className={css.searchRow}>
          <SearchIcon size={14} className={css.searchIcon} />
          <input
            className={css.input}
            role="combobox"
            aria-label={t('palette.placeholder')}
            aria-autocomplete="list"
            aria-expanded
            aria-controls="zen-palette-list"
            placeholder={t('palette.placeholder')}
            value={query}
            autoFocus
            spellCheck={false}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className={css.list} id="zen-palette-list" role="listbox">
          {results.sessions.length > 0 && (
            <div className={css.groupLabel}>{t('palette.sessions')}</div>
          )}
          {results.sessions.map((hit, index) =>
            renderOption({ kind: 'session', hit, disabled: hit.disabled }, optionIndex('session', index)),
          )}
          {results.commands.length > 0 && (
            <div className={css.groupLabel}>{t('palette.commands')}</div>
          )}
          {results.commands.map((hit, index) =>
            renderOption({ kind: 'command', hit, disabled: false }, optionIndex('command', index)),
          )}
          {flat.length === 0 && <div className={css.empty}>{t('palette.empty')}</div>}
        </div>
        <div className={css.hint}>{t('palette.hint')}</div>
      </div>
    </div>
  )
}
