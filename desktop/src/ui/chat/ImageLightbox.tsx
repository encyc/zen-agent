/** Zoomable full-pane preview for an image the app is already showing (the
 * read_image card's preview today). Wheel zooms around the cursor, dragging
 * pans once zoomed, double-click resets, click or Escape closes.
 *
 * `mount` scopes the mask to one pane (the chat pane passes its root here):
 * the backdrop portals in and turns absolute, so the sidebar and the work
 * rail keep their own space. Without it the mask covers the whole window. */

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { pan, resetZoom, zoomAt, type ZoomState } from './imageZoom'
import css from './ImageLightbox.module.css'

/** A click that dragged is a pan, not a close: this delta separates them. */
const DRAG_THRESHOLD_PX = 3

export function ImageLightbox({
  src,
  alt,
  onClose,
  mount = null,
}: {
  src: string
  alt: string
  onClose: () => void
  mount?: HTMLElement | null
}) {
  const [state, setState] = useState<ZoomState>(resetZoom)
  // The wheel handler is attached once, so rapid ticks compound through this
  // ref instead of waiting for the next render to publish `state`.
  const stateRef = useRef(state)
  const backdropRef = useRef<HTMLDivElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const dragRef = useRef<{
    x: number
    y: number
    base: ZoomState
    moved: boolean
  } | null>(null)
  const dragMovedRef = useRef(false)

  const apply = (next: ZoomState) => {
    stateRef.current = next
    setState(next)
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // React attaches wheel listeners passively; zooming must preventDefault
  // (the mask is modal — wheel behind it must not scroll the chat), so this
  // one goes in natively with `passive: false`.
  useEffect(() => {
    const el = backdropRef.current
    if (el === null) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = el.getBoundingClientRect()
      const img = imgRef.current
      const next = zoomAt(
        stateRef.current,
        { x: event.clientX - rect.left - rect.width / 2, y: event.clientY - rect.top - rect.height / 2 },
        event.deltaY,
        { w: rect.width, h: rect.height },
        img === null ? { w: 0, h: 0 } : { w: img.offsetWidth, h: img.offsetHeight },
      )
      apply(next)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const fitSize = () => {
    const img = imgRef.current
    return img === null ? { w: 0, h: 0 } : { w: img.offsetWidth, h: img.offsetHeight }
  }
  const viewportSize = () => {
    const el = backdropRef.current
    return el === null ? { w: 0, h: 0 } : { w: el.clientWidth, h: el.clientHeight }
  }

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { x: event.clientX, y: event.clientY, base: stateRef.current, moved: false }
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (drag === null) return
    const dx = event.clientX - drag.x
    const dy = event.clientY - drag.y
    if (!drag.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) drag.moved = true
    apply(pan(drag.base, { x: dx, y: dy }, viewportSize(), fitSize()))
  }

  const endDrag = () => {
    dragMovedRef.current = dragRef.current?.moved === true
    dragRef.current = null
  }

  const backdrop = (
    <div
      ref={backdropRef}
      className={css.backdrop}
      data-absolute={mount !== null || undefined}
      data-zoomed={state.scale > 1 || undefined}
      role="dialog"
      aria-modal="true"
      aria-label={alt}
      onClick={() => {
        if (!dragMovedRef.current) onClose()
      }}
      onDoubleClick={() => apply(resetZoom())}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <img
        ref={imgRef}
        className={css.image}
        src={src}
        alt={alt}
        draggable={false}
        style={{ transform: `translate(${state.offset.x}px, ${state.offset.y}px) scale(${state.scale})` }}
      />
    </div>
  )
  return mount !== null ? createPortal(backdrop, mount) : backdrop
}
