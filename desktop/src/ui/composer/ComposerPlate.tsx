/** The shared composer plate: capsule, drag-resize, auto-growing draft
 * surface, token boxes, the anchored option menu and the control row. Chat and
 * room adapters keep their own draft state, menus and submit bindings; this
 * file owns the box itself so every mode renders and behaves identically. */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import { useT } from '../../i18n/locale'
import type { TokenBox } from '../../state/mentions'
import { FileIcon, SendArrowUpIcon, StopIcon, XIcon } from '../icons'
import { Tooltip } from '../primitives'
import { TokenBoxes } from './TokenBoxes'
import { clampInputHeight, inputHeightLimits, type InputHeightLimits } from './inputHeight'
import css from './ComposerPlate.module.css'

const AUTO_GROW_MAX = 360

export interface ComposerPrimary {
  kind: 'send' | 'steer' | 'stop'
  disabled: boolean
  onPress(): void
}

export interface ComposerOption {
  key: string
  name: ReactNode
  desc: ReactNode
  kind?: string
  data?: Record<string, string>
}

export interface ComposerPlateProps {
  value: string
  boxes?: TokenBox[]
  placeholder: string
  inputRef: RefObject<HTMLTextAreaElement | null>
  onChange(value: string, caret: number): void
  onCaret(position: number): void
  onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void
  primary: ComposerPrimary
  menu?: ReactNode
  attachments?: ReactNode
  leading?: ReactNode
  trailing?: ReactNode
  plateData?: Record<string, string>
  inputData?: Record<string, string>
}

interface ResizeDrag {
  pointerId: number
  startY: number
  startHeight: number
  previous: number | null
  limits: InputHeightLimits
  current: number
  frame: number
}

/** Limits straight from the laid-out textarea: its resolved line box and
 * padding are the only source of truth for "two lines". */
function limitsFor(area: HTMLTextAreaElement): InputHeightLimits {
  const styles = getComputedStyle(area)
  return inputHeightLimits(
    Number.parseFloat(styles.lineHeight),
    (Number.parseFloat(styles.paddingTop) || 0) +
      (Number.parseFloat(styles.paddingBottom) || 0),
    window.innerHeight,
  )
}

/** Enter submits only when the setting allows it; ⌘/Ctrl+Enter always does.
 * Shared so every mode keeps the same keyboard contract. */
export function isSubmitKey(
  event: React.KeyboardEvent,
  enterToSend: boolean,
): boolean {
  return (
    event.key === 'Enter' &&
    !event.shiftKey &&
    !event.nativeEvent.isComposing &&
    (enterToSend || event.metaKey || event.ctrlKey)
  )
}

export function ComposerRoot({ children }: { children: ReactNode }) {
  return <div className={css.root}>{children}</div>
}

export function ComposerPlate({
  value,
  boxes = [],
  placeholder,
  inputRef,
  onChange,
  onCaret,
  onKeyDown,
  primary,
  menu,
  attachments,
  leading,
  trailing,
  plateData,
  inputData,
}: ComposerPlateProps) {
  const t = useT()
  // null = auto-grow with the draft; a number is the size the user dragged.
  const [manualHeight, setManualHeight] = useState<number | null>(null)
  const [renderedHeight, setRenderedHeight] = useState(0)
  const resizeDrag = useRef<ResizeDrag | null>(null)

  // Auto-grow the textarea unless the user pinned a height by dragging the
  // top edge; then the inline size owns the box until a reset.
  useEffect(() => {
    const area = inputRef.current
    if (area === null || manualHeight !== null) return
    area.style.height = '0px'
    area.style.height = `${Math.min(area.scrollHeight, AUTO_GROW_MAX)}px`
    setRenderedHeight(area.offsetHeight)
  }, [value, manualHeight, inputRef])

  useEffect(
    () => () => {
      document.documentElement.removeAttribute('data-composer-resizing')
    },
    [],
  )

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const area = inputRef.current
    if (event.button !== 0 || area === null) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    const startHeight = area.getBoundingClientRect().height
    resizeDrag.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight,
      previous: manualHeight,
      limits: limitsFor(area),
      current: startHeight,
      frame: 0,
    }
    document.documentElement.setAttribute('data-composer-resizing', 'true')
    event.currentTarget.setPointerCapture(event.pointerId)
    setManualHeight(startHeight)
  }

  const moveResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = resizeDrag.current
    if (state === null || state.pointerId !== event.pointerId) return
    state.current = clampInputHeight(
      state.startHeight + (state.startY - event.clientY),
      state.limits,
    )
    if (state.frame !== 0) return
    state.frame = requestAnimationFrame(() => {
      state.frame = 0
      if (resizeDrag.current === state) setManualHeight(state.current)
    })
  }

  const endResize = (commit: boolean) => {
    const state = resizeDrag.current
    if (state === null) return
    if (state.frame !== 0) cancelAnimationFrame(state.frame)
    resizeDrag.current = null
    document.documentElement.removeAttribute('data-composer-resizing')
    setManualHeight(commit ? state.current : state.previous)
  }

  const onResizeKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const area = inputRef.current
    if (area === null) return
    if (event.key === 'Escape' && resizeDrag.current !== null) {
      event.preventDefault()
      endResize(false)
      return
    }
    const limits = limitsFor(area)
    const base = manualHeight ?? area.getBoundingClientRect().height
    const step = event.shiftKey ? limits.step * 2 : limits.step
    let next: number | null = null
    if (event.key === 'ArrowUp') next = base + step
    else if (event.key === 'ArrowDown') next = base - step
    else if (event.key === 'Home') next = limits.min
    else if (event.key === 'End') next = limits.max
    if (next === null) return
    event.preventDefault()
    setManualHeight(clampInputHeight(next, limits))
  }

  const primaryLabel =
    primary.kind === 'stop'
      ? t('composer.stop')
      : primary.kind === 'steer'
        ? t('composer.steer')
        : t('composer.send')

  return (
    <div
      className={css.card}
      {...(plateData as unknown as React.HTMLAttributes<HTMLDivElement>)}
    >
      <div
        className={css.resizeHandle}
        role="separator"
        aria-orientation="horizontal"
        aria-label={t('composer.resizeHeight')}
        title={t('composer.resizeHint')}
        aria-valuenow={manualHeight ?? renderedHeight}
        aria-valuetext={`${manualHeight ?? renderedHeight}px`}
        tabIndex={0}
        onPointerDown={startResize}
        onPointerMove={moveResize}
        onPointerUp={(event) => {
          if (resizeDrag.current?.pointerId !== event.pointerId) return
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
          endResize(true)
        }}
        onPointerCancel={(event) => {
          if (resizeDrag.current?.pointerId !== event.pointerId) return
          endResize(false)
        }}
        onDoubleClick={() => setManualHeight(null)}
        onKeyDown={onResizeKeyDown}
      />
      {attachments}
      <div
        className={css.scroll}
        style={manualHeight === null ? undefined : { maxHeight: manualHeight }}
      >
        <div className={css.inputWrap}>
          <textarea
            ref={inputRef as React.RefObject<HTMLTextAreaElement>}
            className={css.input}
            style={
              manualHeight === null
                ? undefined
                : { height: manualHeight, minHeight: 0, maxHeight: manualHeight }
            }
            value={value}
            onChange={(event) => onChange(event.target.value, event.target.selectionStart)}
            onSelect={(event) => onCaret(event.currentTarget.selectionStart)}
            onClick={(event) => onCaret(event.currentTarget.selectionStart)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            rows={1}
            spellCheck={false}
            dir="auto"
            {...(inputData as unknown as React.TextareaHTMLAttributes<HTMLTextAreaElement>)}
          />
          <TokenBoxes value={value} boxes={boxes} textareaRef={inputRef} />
        </div>
      </div>
      {menu}
      <div className={css.row}>
        <div className={css.tools}>{leading}</div>
        <div className={css.trailing}>
          {trailing}
          <Tooltip label={primaryLabel}>
            <button
              type="button"
              className={css.primary}
              aria-label={primaryLabel}
              disabled={primary.disabled}
              onClick={primary.onPress}
            >
              {primary.kind === 'stop' ? <StopIcon size={14} /> : <SendArrowUpIcon size={15} />}
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}

/** The anchored option list shared by the slash menu and the room's @ menu. */
export function ComposerMenu({
  options,
  activeIndex,
  onHover,
  onPick,
}: {
  options: ComposerOption[]
  activeIndex: number
  onHover(index: number): void
  onPick(index: number): void
}) {
  return (
    <div className={css.menu} role="listbox">
      {options.map((option, index) => (
        <button
          key={option.key}
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          className={css.menuItem}
          data-active={index === activeIndex || undefined}
          data-kind={option.kind}
          {...(option.data as unknown as React.ButtonHTMLAttributes<HTMLButtonElement>)}
          onMouseEnter={() => onHover(index)}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => onPick(index)}
        >
          <span className={css.menuName}>{option.name}</span>
          <span className={css.menuDesc}>{option.desc}</span>
        </button>
      ))}
    </div>
  )
}

export function ComposerAttachments({
  items,
  onRemove,
}: {
  items: readonly { id: string; name: string }[]
  onRemove(id: string): void
}) {
  return (
    <div className={css.attachRail}>
      {items.map((item) => (
        <div key={item.id} className={css.attachChip}>
          <span className={css.attachThumb} aria-hidden>
            <FileIcon size={12} />
          </span>
          <span className={css.attachName}>{item.name}</span>
          <button
            type="button"
            className={css.attachRemove}
            aria-label="remove attachment"
            onClick={() => onRemove(item.id)}
          >
            <XIcon size={10} />
          </button>
        </div>
      ))}
    </div>
  )
}
