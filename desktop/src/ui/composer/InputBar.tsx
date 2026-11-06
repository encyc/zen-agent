/** The composer capsule (dsh InputBar shape): attachments rail, auto-growing
 * draft surface, control row (attach · mode | context · model+thinking ·
 * send/stop), slash-command menu, plus the pending lanes and todo docks
 * stacked above the plate.
 * Enter submits; while running it steers; Esc interrupts. */

import { useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { listen } from '@tauri-apps/api/event'
import { useAppStore } from '../../state/appStore'
import { useApprovalStore, useAskStore, useComposerStore, useSettingsStore, useStatusStore, useCatalogStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import { AskCard } from '../ask/AskCard'
import { pickImagePaths } from './pickImages'
import { TodoDock, PendingDock, ContextMeter } from './Docks'
import { ModelMenu, ModeMenu } from './Menus'
import { ChevronDownIcon, FileIcon, PlusIcon, SendArrowUpIcon, StopIcon, XIcon } from '../icons'
import { Tooltip } from '../primitives'
import css from './InputBar.module.css'

interface MenuState {
  kind: 'model' | 'mode' | null
}

export function InputBar() {
  const t = useT()
  const busy = useAppStore((state) => state.busy)
  const submit = useAppStore((state) => state.submit)
  const cancel = useAppStore((state) => state.cancel)
  const steer = useAppStore((state) => state.steer)
  const setMode = useAppStore((state) => state.setMode)
  const status = useStatusStore((state) => state.status)
  const mode = status?.mode ?? 'zen'
  const model = status?.model ?? ''
  const thinking = status?.thinking ?? null

  const draft = useComposerStore((state) => state.draft)
  const setDraft = useComposerStore((state) => state.setDraft)
  const images = useComposerStore((state) => state.images)
  const addImages = useComposerStore((state) => state.addImages)
  const removeImage = useComposerStore((state) => state.removeImage)
  const commands = useCatalogStore((state) => state.commands)
  const enterToSend = useSettingsStore((state) => state.enterToSend)
  const approvalCount = useApprovalStore((state) => state.queue.length)
  const askCount = useAskStore((state) => state.queue.length)

  const [menu, setMenu] = useState<MenuState>({ kind: null })
  const [slashIndex, setSlashIndex] = useState(0)
  const textRef = useRef<HTMLTextAreaElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const empty = draft.trim() === '' && images.length === 0
  const isSlash = draft.startsWith('/') && !draft.includes('\n')
  const slashQuery = isSlash ? draft.slice(1) : ''
  const slashMatches = useMemo(() => {
    if (!isSlash) return []
    const query = slashQuery.split(' ')[0].toLowerCase()
    return commands.filter((command) => command.name.startsWith(query)).slice(0, 8)
  }, [commands, isSlash, slashQuery])

  // Auto-grow the textarea (dsh: the scrollport caps at 14 lines).
  useEffect(() => {
    const area = textRef.current
    if (area === null) return
    area.style.height = '0px'
    area.style.height = `${Math.min(area.scrollHeight, 360)}px`
  }, [draft])

  // Tauri drag-drop: file drop events carry absolute paths.
  useEffect(() => {
    let disposed = false
    const promise = listen<{ paths: string[]; type: string }>('tauri://drag-drop', (event) => {
      const paths = (event.payload as unknown as { paths?: string[] }).paths
      if (Array.isArray(paths)) {
        const imagePaths = paths.filter((path) => /\.(png|jpe?g|gif|webp|bmp)$/i.test(path))
        if (imagePaths.length > 0) addImages(imagePaths)
      }
    })
    return () => {
      disposed = true
      void promise.then((unlisten) => {
        if (!disposed) unlisten()
      })
    }
  }, [addImages])

  const doSubmit = () => {
    setMenu({ kind: null })
    if (busy) {
      const text = draft.trim()
      if (text === '') return
      void steer(text)
      return
    }
    void submit()
    requestAnimationFrame(() => textRef.current?.focus())
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Slash menu navigation owns the keys first.
    if (isSlash && slashMatches.length > 0 && menu.kind === null) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setSlashIndex((index) => (index + 1) % slashMatches.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setSlashIndex((index) => (index - 1 + slashMatches.length) % slashMatches.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        const picked = slashMatches[slashIndex] ?? slashMatches[0]
        if (picked !== undefined) {
          event.preventDefault()
          setDraft(`/${picked.name} `)
          setSlashIndex(0)
          return
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setDraft('')
        return
      }
    }
    if (
      event.key === 'Enter' &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing &&
      (enterToSend || event.metaKey || event.ctrlKey)
    ) {
      event.preventDefault()
      doSubmit()
      return
    }
    if (event.key === 'Escape' && busy) {
      event.preventDefault()
      void cancel()
    }
  }

  const placeholder = busy
    ? t('composer.placeholder.busy')
    : mode === 'plan'
      ? t('composer.placeholder.plan')
      : t('composer.placeholder')

  return (
    <div className={css.root}>
      {approvalCount > 0 && <ApprovalPanel />}
      {askCount > 0 && <AskCard />}
      <PendingDock />
      <TodoDock />
      <div className={css.card} data-composer-plate>
        {images.length > 0 && (
            <div className={css.attachRail}>
              {images.map((image) => (
                <div key={image.id} className={css.attachChip}>
                  <span className={css.attachThumb} aria-hidden>
                    <FileIcon size={12} />
                  </span>
                  <span className={css.attachName}>{image.name}</span>
                  <button
                    type="button"
                    className={css.attachRemove}
                    aria-label="remove attachment"
                    onClick={() => removeImage(image.id)}
                  >
                    <XIcon size={10} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className={css.scroll} ref={scrollRef}>
            <textarea
              ref={textRef}
              className={css.input}
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
                setSlashIndex(0)
              }}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              rows={1}
              spellCheck={false}
              dir="auto"
            />
          </div>
          {isSlash && slashMatches.length > 0 && menu.kind === null && (
            <div className={css.slashMenu} role="listbox">
              {slashMatches.map((command, index) => (
                <button
                  key={command.name}
                  type="button"
                  role="option"
                  aria-selected={index === slashIndex}
                  className={css.slashItem}
                  data-active={index === slashIndex || undefined}
                  onMouseEnter={() => setSlashIndex(index)}
                  onClick={() => {
                    setDraft(`/${command.name} `)
                    textRef.current?.focus()
                  }}
                >
                  <span className={css.slashName}>/{command.name}</span>
                  <span className={css.slashDesc}>{command.description}</span>
                </button>
              ))}
            </div>
          )}
          <div className={css.row}>
            <div className={css.tools}>
              <Tooltip label={t('composer.attach')}>
                <button
                  type="button"
                  className={css.add}
                  aria-label={t('composer.attach')}
                  onClick={() => {
                    void pickImagePaths()
                      .then((paths) => {
                        if (paths.length > 0) addImages(paths)
                      })
                      .catch(() => {})
                  }}
                >
                  <PlusIcon size={14} />
                </button>
              </Tooltip>
              <div className={css.modes}>
                <span className={css.anchor}>
                  <ModeChip mode={mode} onOpen={() => setMenu({ kind: 'mode' })} />
                  {menu.kind === 'mode' && (
                    <div className={css.menuLayer}>
                      <ModeMenu
                        mode={mode}
                        onPick={(picked) => {
                          void setMode(picked)
                          setMenu({ kind: null })
                        }}
                        onClose={() => setMenu({ kind: null })}
                      />
                    </div>
                  )}
                </span>
              </div>
            </div>
            <div className={css.trailing}>
              <ContextMeter />
              <span className={css.anchor} data-align="right">
                <ModelChip model={model} thinking={thinking} onOpen={() => setMenu({ kind: 'model' })} />
                {menu.kind === 'model' && (
                  <div className={css.menuLayer}>
                    <ModelMenu
                      thinking={thinking}
                      onThinkingPick={(level) => {
                        void useAppStore.getState().setThinking(level)
                      }}
                      onClose={() => setMenu({ kind: null })}
                    />
                  </div>
                )}
              </span>
              <Tooltip label={busy ? t('composer.stop') : empty ? t('composer.send') : t('composer.steer')}>
                <button
                  type="button"
                  className={css.primary}
                  aria-label={busy ? t('composer.stop') : t('composer.send')}
                  disabled={!busy && empty}
                  onMouseDown={(event) => {
                    if (busy && empty) {
                      event.preventDefault()
                      void cancel()
                    }
                  }}
                  onClick={() => {
                    if (busy && empty) void cancel()
                    else doSubmit()
                  }}
                >
                  {busy && empty ? <StopIcon size={14} /> : <SendArrowUpIcon size={15} />}
                </button>
              </Tooltip>
            </div>
          </div>
        </div>
    </div>
  )
}

function ModeChip({ mode, onOpen }: { mode: string; onOpen(): void }) {
  const t = useT()
  return (
    <button
      type="button"
      className={clsx(css.chip, css.modeChip)}
      data-menu-trigger="mode"
      onClick={onOpen}
    >
      <span className={css.chipLabel} data-mode={mode}>{t(`status.mode.${mode}`)}</span>
      <ChevronDownIcon size={11} />
    </button>
  )
}

function ModelChip({
  model,
  thinking,
  onOpen,
}: {
  model: string
  thinking: string | null
  onOpen(): void
}) {
  const t = useT()
  const level = thinking !== null && thinking !== 'off' ? thinking : null
  return (
    <button
      type="button"
      className={css.chip}
      data-menu-trigger="model"
      aria-label={
        level === null
          ? `${t('menu.model')}: ${model}`
          : `${t('menu.model')}: ${model}. ${t('menu.thinking')}: ${level}`
      }
      onClick={onOpen}
    >
      <span className={css.chipLabel}>{model}</span>
      {level !== null ? <span className={css.thinkingBadge}>{level}</span> : null}
      <ChevronDownIcon size={11} />
    </button>
  )
}

export { clsx }
