/** Dual edge handles for the centered conversation band. Both sides change one
 * preferred max-width; dragging previews it and commits on release. */

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import {
  CHAT_WIDTH_DEFAULT,
  CHAT_WIDTH_MAX,
  CHAT_WIDTH_MIN,
  clampChatWidth,
} from '../columns'
import { useT } from '../../i18n/locale'
import css from './ChatWidthHandles.module.css'

export interface ChatWidthHandlesProps {
  width: number
  onChange(width: number): void
}

type Side = 'left' | 'right'

interface DragState {
  pointerId: number
  side: Side
  startX: number
  startWidth: number
  current: number
  frame: number
}

function applyWidth(width: number): void {
  document.documentElement.style.setProperty('--ds-chat-content-width', `${width}px`)
}

export function ChatWidthHandles({ width, onChange }: ChatWidthHandlesProps) {
  const t = useT()
  const [preview, setPreview] = useState<number | null>(null)
  const drag = useRef<DragState | null>(null)
  const live = preview ?? width

  useEffect(
    () => () => {
      document.documentElement.removeAttribute('data-chat-resizing')
    },
    [],
  )

  const end = (commit: boolean) => {
    const state = drag.current
    if (state === null) return
    if (state.frame !== 0) cancelAnimationFrame(state.frame)
    drag.current = null
    document.documentElement.removeAttribute('data-chat-resizing')
    const final = commit ? state.current : state.startWidth
    applyWidth(final)
    setPreview(null)
    if (commit && final !== width) onChange(final)
  }

  const start = (side: Side) => (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    drag.current = {
      pointerId: event.pointerId,
      side,
      startX: event.clientX,
      startWidth: width,
      current: width,
      frame: 0,
    }
    document.documentElement.setAttribute('data-chat-resizing', 'true')
    event.currentTarget.setPointerCapture(event.pointerId)
    setPreview(width)
  }

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (state === null || state.pointerId !== event.pointerId) return
    const towardWider = state.side === 'left' ? state.startX - event.clientX : event.clientX - state.startX
    state.current = clampChatWidth(state.startWidth + towardWider * 2)
    if (state.frame !== 0) return
    state.frame = requestAnimationFrame(() => {
      state.frame = 0
      if (drag.current !== state) return
      applyWidth(state.current)
      setPreview(state.current)
    })
  }

  const keyDown = (side: Side) => (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && drag.current !== null) {
      event.preventDefault()
      end(false)
      return
    }
    const step = event.shiftKey ? 32 : 16
    let next: number | null = null
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const wider =
        (side === 'left' && event.key === 'ArrowLeft') ||
        (side === 'right' && event.key === 'ArrowRight')
      next = width + (wider ? step : -step)
    } else if (event.key === 'Home') {
      next = CHAT_WIDTH_DEFAULT
    } else if (event.key === 'End') {
      next = CHAT_WIDTH_MAX
    }
    if (next === null) return
    event.preventDefault()
    onChange(clampChatWidth(next))
  }

  return (
    <div className={css.host} data-chat-width-handles>
      {(['left', 'right'] as const).map((side) => (
        <div
          key={side}
          className={css.handle}
          data-side={side}
          role="separator"
          aria-orientation="vertical"
          aria-label={t('chat.resizeWidth')}
          aria-valuemin={CHAT_WIDTH_MIN}
          aria-valuemax={CHAT_WIDTH_MAX}
          aria-valuenow={live}
          aria-valuetext={`${live}px`}
          tabIndex={0}
          onPointerDown={start(side)}
          onPointerMove={move}
          onPointerUp={(event) => {
            if (drag.current?.pointerId !== event.pointerId) return
            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId)
            }
            end(true)
          }}
          onPointerCancel={(event) => {
            if (drag.current?.pointerId !== event.pointerId) return
            end(false)
          }}
          onDoubleClick={() => onChange(CHAT_WIDTH_DEFAULT)}
          onKeyDown={keyDown(side)}
        />
      ))}
    </div>
  )
}
