/** Work panel — pi's docked right rail: a 46px header with review/context
 * tabs, a close control and a resizable left edge. The old session inspector
 * (todos, jobs, subagents, errors) is gone; those live in the docks. */

import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useChatStore } from '../../state/chatStore'
import { useStatusStore, useWorkPanelStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import {
  RIGHTBAR_DEFAULT,
  RIGHTBAR_MAX_RATIO,
  RIGHTBAR_MIN,
  clampWidth,
} from '../columns'
import {
  ActivityIcon,
  BotIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileIcon,
  XIcon,
} from '../icons'
import { collectFileChanges, fileBasename } from './review'
import { SubagentTranscript } from './SubagentTranscript'
import css from './RightPanel.module.css'

export interface RightPanelProps {
  width: number
  onWidth(width: number): void
  onClose(): void
}

interface DragState {
  pointerId: number
  startX: number
  startWidth: number
  frame: number
}

function clampPanel(px: number): number {
  return clampWidth(px, RIGHTBAR_MIN, Math.max(RIGHTBAR_MIN, window.innerWidth * RIGHTBAR_MAX_RATIO))
}

export function RightPanel({ width, onWidth, onClose }: RightPanelProps) {
  const t = useT()
  const status = useStatusStore((state) => state.status)
  const nodes = useChatStore((state) => state.nodes)
  const tab = useWorkPanelStore((state) => state.tab)
  const subagent = useWorkPanelStore((state) => state.subagent)
  const showTab = useWorkPanelStore((state) => state.showTab)
  const openSubagent = useWorkPanelStore((state) => state.openSubagent)
  const closeSubagent = useWorkPanelStore((state) => state.closeSubagent)
  const subagentName =
    subagent === null
      ? ''
      : status?.subagents?.find((entry) => entry.call_id === subagent.callId)?.name ||
        subagent.name
  const [openPath, setOpenPath] = useState<string | null>(null)
  const drag = useRef<DragState | null>(null)

  const changes = useMemo(() => collectFileChanges(nodes), [nodes])

  const stopDrag = () => {
    const state = drag.current
    if (state === null) return
    if (state.frame !== 0) cancelAnimationFrame(state.frame)
    drag.current = null
    document.documentElement.removeAttribute('data-work-panel-resizing')
  }

  const start = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: width,
      frame: 0,
    }
    document.documentElement.setAttribute('data-work-panel-resizing', 'true')
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (state === null || state.pointerId !== event.pointerId) return
    const next = clampPanel(state.startWidth + (state.startX - event.clientX))
    if (state.frame !== 0) cancelAnimationFrame(state.frame)
    state.frame = requestAnimationFrame(() => {
      state.frame = 0
      if (drag.current === state) onWidth(next)
    })
  }

  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 32 : 16
    let next: number | null = null
    if (event.key === 'ArrowLeft') next = width + step
    else if (event.key === 'ArrowRight') next = width - step
    else if (event.key === 'Home') next = RIGHTBAR_DEFAULT
    if (next === null) return
    event.preventDefault()
    onWidth(clampPanel(next))
  }

  const usage = status?.usage ?? { input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 }
  const contextUsed = status?.context_used ?? 0
  const contextWindow = status?.context_window ?? 0

  return (
    <div className={css.root} data-work-panel data-tab={tab}>
      <div
        className={css.resize}
        role="separator"
        aria-orientation="vertical"
        aria-label={t('chat.resizeWidth')}
        aria-valuemin={RIGHTBAR_MIN}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={(event) => {
          if (drag.current?.pointerId !== event.pointerId) return
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
          stopDrag()
        }}
        onPointerCancel={(event) => {
          if (drag.current?.pointerId !== event.pointerId) return
          stopDrag()
        }}
        onDoubleClick={() => onWidth(RIGHTBAR_DEFAULT)}
        onKeyDown={keyDown}
      />
      <div className={css.header}>
        <div className={css.tabs} role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'review'}
            className={css.tab}
            onClick={() => showTab('review')}
          >
            <FileIcon size={13} />
            <span>{t('panel.review')}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'context'}
            className={css.tab}
            onClick={() => showTab('context')}
          >
            <ActivityIcon size={13} />
            <span>{t('panel.context')}</span>
          </button>
          {subagent !== null && (
            <div
              className={css.subagentTab}
              data-active={tab === 'subagent' || undefined}
              role="presentation"
            >
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'subagent'}
                className={css.tab}
                title={subagentName}
                onClick={() => openSubagent(subagent)}
              >
                <BotIcon size={13} />
                <span className={css.tabLabel}>{subagentName}</span>
              </button>
              <button
                type="button"
                className={css.tabClose}
                aria-label={t('panel.subagentClose')}
                onClick={closeSubagent}
              >
                <XIcon size={11} />
              </button>
            </div>
          )}
        </div>
        <button
          type="button"
          className={css.close}
          aria-label={t('panel.close')}
          onClick={onClose}
        >
          <XIcon size={13} />
        </button>
      </div>
      <div
        className={css.body}
        data-selectable
        data-embed={tab === 'subagent' && subagent !== null ? '' : undefined}
      >
        {tab === 'subagent' && subagent !== null ? (
          <SubagentTranscript target={subagent} />
        ) : tab === 'review' ? (
          changes.length === 0 ? (
            <p className={css.empty}>{t('panel.reviewEmpty')}</p>
          ) : (
            <ul className={css.files}>
              {changes.map((change) => {
                const open = openPath === change.path
                return (
                  <li key={change.path} className={css.file}>
                    <button
                      type="button"
                      className={css.fileRow}
                      aria-expanded={open}
                      title={change.path}
                      onClick={() => setOpenPath(open ? null : change.path)}
                    >
                      <span className={css.chevron} data-open={open || undefined}>
                        {open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
                      </span>
                      <span className={css.fileName}>{fileBasename(change.path)}</span>
                      <span className={css.additions}>+{change.additions}</span>
                      <span className={css.removals}>-{change.removals}</span>
                    </button>
                    {open && <pre className={css.diff}>{change.diffs.join('\n')}</pre>}
                  </li>
                )
              })}
            </ul>
          )
        ) : (
          <div className={css.rows}>
            <div className={css.row} data-testid="context-used">
              <span className={css.rowLabel}>{t('usage.context')}</span>
              <span className={css.rowValue}>
                {formatCount(contextUsed)} / {formatCount(contextWindow)}
              </span>
            </div>
            <div className={css.row}>
              <span className={css.rowLabel}>{t('panel.cacheHit')}</span>
              <span className={css.rowValue}>
                {status?.cache_hit_ratio !== null && status?.cache_hit_ratio !== undefined
                  ? `${Math.round(status.cache_hit_ratio * 100)}%`
                  : '—'}
              </span>
            </div>
            <div className={css.row}>
              <span className={css.rowLabel}>{t('usage.turn')}</span>
              <span className={css.rowValue}>
                {status?.turn_ms !== null && status?.turn_ms !== undefined && status.turn_ms > 0
                  ? formatDuration(status.turn_ms)
                  : '—'}
              </span>
            </div>
            <div className={css.row}>
              <span className={css.rowLabel}>{t('panel.tokensInOut')}</span>
              <span className={css.rowValue}>
                {formatCount(usage.input)} / {formatCount(usage.output)}
              </span>
            </div>
            <div className={css.row}>
              <span className={css.rowLabel}>{t('panel.cacheRW')}</span>
              <span className={css.rowValue}>
                {formatCount(usage.cache_read)} / {formatCount(usage.cache_write)}
              </span>
            </div>
            <div className={css.row}>
              <span className={css.rowLabel}>{t('usage.cost')}</span>
              <span className={css.rowValue}>${usage.cost.toFixed(4)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '0'
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m`
}
