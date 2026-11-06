/** Three-column shell (dsh AppFrame equivalent): sidebar | center | rightbar
 * with pointer-capture drag handles and the same column solve. The center
 * holds the conversation; the right column is a track the status panel asks
 * for. */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  CENTER_MIN,
  RIGHTBAR_MAX_RATIO,
  RIGHTBAR_MIN,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  computeColumns,
} from './columns'
import css from './AppFrame.module.css'

interface AppFrameProps {
  sidebarOpen: boolean
  sidebarWidth: number
  onSidebarWidth(width: number): void
  onSidebarToggle(): void
  rightbarOpen: boolean
  rightbarWidth: number
  onRightbarWidth(width: number): void
  sidebar: ReactNode
  rightbar: ReactNode
  bottom?: ReactNode
  children: ReactNode
}

export function AppFrame(props: AppFrameProps) {
  const frameRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState(() => window.innerWidth)

  useEffect(() => {
    let raf: number | null = null
    const observer = new ResizeObserver(() => {
      raf ??= requestAnimationFrame(() => {
        raf = null
        setViewport(frameRef.current?.getBoundingClientRect().width ?? window.innerWidth)
      })
    })
    if (frameRef.current) observer.observe(frameRef.current)
    return () => observer.disconnect()
  }, [])

  const sidebarCollapsed = props.sidebarOpen === false
  const sidebarPreference = sidebarCollapsed ? 0 : props.sidebarWidth || 280
  const rightbarPreference = props.rightbarOpen ? props.rightbarWidth || 380 : 0
  const cols = computeColumns(viewport, sidebarPreference, rightbarPreference)

  // Drag handles: pointer capture + rAF throttled deltas, frozen base.
  const [dragging, setDragging] = useState(false)
  const sidebarBase = useRef(0)
  const rightbarBase = useRef(0)
  const frame = useRef<number | null>(null)

  const endDrag = useCallback(() => {
    if (frame.current !== null) {
      cancelAnimationFrame(frame.current)
      frame.current = null
    }
    setDragging(false)
  }, [])

  const sidebarDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, start: boolean) => {
      if (start) {
        sidebarBase.current = cols.sidebar
        setDragging(true)
        return
      }
      const dx = event.clientX - sidebarBase.current
      if (frame.current !== null) cancelAnimationFrame(frame.current)
      frame.current = requestAnimationFrame(() => {
        frame.current = null
        props.onSidebarWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, sidebarBase.current + dx)))
      })
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cols.sidebar, props.onSidebarWidth],
  )

  const rightbarDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, start: boolean) => {
      if (start) {
        rightbarBase.current = cols.rightbar
        setDragging(true)
        return
      }
      const dx = rightbarBase.current - event.clientX
      if (frame.current !== null) cancelAnimationFrame(frame.current)
      frame.current = requestAnimationFrame(() => {
        frame.current = null
        props.onRightbarWidth(Math.max(RIGHTBAR_MIN, Math.min(viewport * RIGHTBAR_MAX_RATIO, rightbarBase.current + dx)))
      })
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cols.rightbar, props.onRightbarWidth, viewport],
  )

  return (
    <div
      ref={frameRef}
      className={css.frame}
      style={{
        gridTemplateColumns: `${cols.sidebar}px minmax(${cols.rightbar === 0 ? 0 : CENTER_MIN}px, 1fr) minmax(0px, ${
          cols.rightbar === 0 ? 0 : cols.rightbar
        }px)`,
      }}
      data-sidebar-collapsed={sidebarCollapsed || undefined}
      data-dragging={dragging || undefined}
    >
      <div className={css.sidebarCol}>{props.sidebar}</div>
      <div className={css.centerCol}>{props.children}</div>
      {cols.rightbar > 0 && <div className={css.rightbarCol}>{props.rightbar}</div>}
      {props.bottom !== undefined && (
        <div className={css.bottomRow} data-shell-bottom>{props.bottom}</div>
      )}
      {!sidebarCollapsed && (
        <div
          className={css.handle}
          data-side="sidebar"
          style={{ left: cols.sidebar }}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.preventDefault()
            event.currentTarget.setPointerCapture(event.pointerId)
            sidebarDrag(event, true)
          }}
          onPointerMove={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) sidebarDrag(event, false)
          }}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        />
      )}
      {cols.rightbar > 0 && (
        <div
          className={css.handle}
          data-side="rightbar"
          style={{ left: viewport - cols.rightbar }}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.preventDefault()
            event.currentTarget.setPointerCapture(event.pointerId)
            rightbarDrag(event, true)
          }}
          onPointerMove={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) rightbarDrag(event, false)
          }}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        />
      )}
    </div>
  )
}
