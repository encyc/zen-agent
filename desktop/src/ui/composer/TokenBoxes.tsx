/** Boxes the recognized `/command`, `/skill:name` and `@path` tokens inside
 * the composer textarea. The real text stays in the native textarea (IME,
 * selection and copy untouched); a hidden mirror with the same metrics
 * measures where each token lands, and an overlay paints the frame. */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { TokenBox } from '../../state/mentions'
import css from './TokenBoxes.module.css'

interface BoxRect {
  left: number
  top: number
  width: number
  height: number
  kind: string
}

const BOX_PAD = 2

function segments(value: string, boxes: TokenBox[]): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  let at = 0
  boxes.forEach((box, index) => {
    if (box.start > at) parts.push(value.slice(at, box.start))
    parts.push(
      <span key={`token-${index}`} data-token-box data-kind={box.kind}>
        {value.slice(box.start, box.end)}
      </span>,
    )
    at = box.end
  })
  if (at < value.length) parts.push(value.slice(at))
  return parts
}

export function TokenBoxes({
  value,
  boxes,
  textareaRef,
}: {
  value: string
  boxes: TokenBox[]
  textareaRef: RefObject<HTMLTextAreaElement | null>
}) {
  const layerRef = useRef<HTMLDivElement | null>(null)
  const mirrorRef = useRef<HTMLDivElement | null>(null)
  const [rects, setRects] = useState<BoxRect[]>([])

  const measure = () => {
    const layer = layerRef.current
    const mirror = mirrorRef.current
    if (layer === null || mirror === null) return
    const origin = layer.getBoundingClientRect()
    const next: BoxRect[] = []
    for (const span of mirror.querySelectorAll<HTMLElement>('[data-token-box]')) {
      for (const rect of span.getClientRects()) {
        if (rect.width === 0) continue
        next.push({
          left: rect.left - origin.left,
          top: rect.top - origin.top,
          width: rect.width,
          height: rect.height,
          kind: span.dataset.kind ?? '',
        })
      }
    }
    setRects(next)
  }

  useLayoutEffect(() => {
    measure()
  }, [value, boxes])

  useEffect(() => {
    const layer = layerRef.current
    if (layer === null) return
    const observer = new ResizeObserver(() => measure())
    observer.observe(layer)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const area = textareaRef.current
    const mirror = mirrorRef.current
    if (area === null || mirror === null) return
    const sync = () => {
      mirror.scrollTop = area.scrollTop
      mirror.scrollLeft = area.scrollLeft
      measure()
    }
    area.addEventListener('scroll', sync)
    return () => area.removeEventListener('scroll', sync)
  }, [textareaRef])

  return (
    <div ref={layerRef} className={css.layer} aria-hidden="true">
      <div ref={mirrorRef} className={css.mirror}>
        {segments(value, boxes)}
      </div>
      {rects.map((rect, index) => (
        <span
          key={`box-${index}`}
          className={css.box}
          data-token-kind={rect.kind}
          style={{
            left: rect.left - BOX_PAD,
            top: rect.top - BOX_PAD,
            width: rect.width + BOX_PAD * 2,
            height: rect.height + BOX_PAD * 2,
          }}
        />
      ))}
    </div>
  )
}
