/** Composer docks: todo panel, pending lanes (queue/steer), context meter —
 * the dsh composer.dock entries, driven by the status snapshot. */

import { useEffect, useId, useRef, useState } from 'react'
import clsx from 'clsx'
import { useAppStore } from '../../state/appStore'
import {
  canDequeuePending,
  canPromotePending,
  queuedNewestFirst,
} from '../../state/pending'
import { useStatusStore } from '../../state/stores'
import type { PendingMessage } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { formatCompactTokens } from '../format'
import { ChevronDownIcon, ListTodoIcon } from '../icons'
import { StateDot } from '../primitives'
import css from './Docks.module.css'

// Selectors must return a stable reference: a fresh `[]` fallback makes
// useSyncExternalStore see a new snapshot on every read (render loop).
const NO_TODOS: never[] = []
const NO_PENDING: never[] = []

export function TodoDock() {
  const t = useT()
  const todos = useStatusStore((state) => state.status?.todos ?? NO_TODOS)
  const [open, setOpen] = useState(false)
  if (todos.length === 0) return null
  const inProgress = todos.filter((todo) => todo.status === 'in_progress').length
  return (
    <div className={css.todoDock}>
      <button type="button" className={css.todoHead} onClick={() => setOpen((value) => !value)}>
        <ListTodoIcon size={12} />
        <span>{t('status.todos')}</span>
        {inProgress > 0 && <StateDot state="running" size={6} />}
        <span className={css.todoCount}>{todos.length}</span>
        <span className={clsx(css.todoChevron, open && css.todoChevronOpen)}>
          <ChevronDownIcon size={11} />
        </span>
      </button>
      {open && (
        <div className={css.todoList}>
          {todos.map((todo, index) => (
            <div key={index} className={css.todoItem}>
              <StateDot
                state={
                  todo.status === 'completed'
                    ? 'ok'
                    : todo.status === 'in_progress'
                      ? 'running'
                      : 'idle'
                }
                size={7}
              />
              <span
                className={css.todoText}
                data-done={todo.status === 'completed' || undefined}
              >
                {String(todo.text ?? todo.description ?? '')}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function PendingDock() {
  const t = useT()
  const pending = useStatusStore((state) => state.status?.pending ?? NO_PENDING)
  const running = useStatusStore((state) => state.status?.running ?? false)
  const promotePending = useAppStore((state) => state.promotePending)
  const dequeuePending = useAppStore((state) => state.dequeuePending)
  const clearPending = useAppStore((state) => state.clearPending)
  if (pending.length === 0) return null
  const queued = queuedNewestFirst(pending)
  const steering = pending.filter((item) => item.lane === 'steer')
  const headlineQueued = queued[0]
  return (
    <div className={css.pendingDock}>
      {steering.length > 0 && (
        <PendingGroup
          lane="steer"
          label={t('composer.steering')}
          items={steering}
          onClear={() => void clearPending()}
        />
      )}
      {queued.length > 0 && (
        <PendingGroup
          lane="queue"
          label={t('composer.queue')}
          items={queued}
          onPromote={
            headlineQueued !== undefined && canPromotePending(headlineQueued, running)
              ? () => void promotePending(headlineQueued.id)
              : undefined
          }
          onDequeue={canDequeuePending(pending) ? () => void dequeuePending() : undefined}
          onClear={() => void clearPending()}
        />
      )}
    </div>
  )
}

function PendingGroup({
  lane,
  label,
  items,
  onPromote,
  onDequeue,
  onClear,
}: {
  lane: string
  label: string
  items: PendingMessage[]
  onPromote?: () => void
  onDequeue?: () => void
  onClear?: () => void
}) {
  const t = useT()
  return (
    <div className={css.pendingGroup} data-lane={lane}>
      <span className={css.pendingLabel}>{label} · {items.length}</span>
      <span className={css.pendingText}>{items[0]?.text ?? ''}</span>
      {onPromote !== undefined && (
        <button type="button" className={css.pendingAction} onClick={onPromote}>
          {t('queue.promote')}
        </button>
      )}
      {onDequeue !== undefined && (
        <button type="button" className={css.pendingAction} onClick={onDequeue}>
          {t('queue.dequeue')}
        </button>
      )}
      {onClear !== undefined && (
        <button type="button" className={css.pendingAction} onClick={onClear}>
          {t('queue.clear')}
        </button>
      )}
    </div>
  )
}

const CONTEXT_RING_RADIUS = 9
const CONTEXT_RING_CIRCUMFERENCE = 2 * Math.PI * CONTEXT_RING_RADIUS
// The card sits above the trigger with a small gap, so crossing it fires a
// leave/enter pair. A short grace keeps the card open while the pointer
// travels between the two.
const CONTEXT_CLOSE_DELAY_MS = 120

export function ContextMeter() {
  const t = useT()
  const status = useStatusStore((state) => state.status)
  const [open, setOpen] = useState(false)
  const closeTimer = useRef<number | null>(null)
  const cardId = useId()
  const used = status?.context_used ?? null
  const window_ = status?.context_window ?? null
  const report = status?.context ?? null

  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    },
    [],
  )

  if (used === null || window_ === null || window_ <= 0 || used <= 0) return null
  const ratio = Math.min(1, used / window_)
  // pi colors capacity by what is left, so the warning keeps one meaning even
  // though the trigger leads with the used figure.
  const level = ratio >= 0.9 ? 'critical' : ratio >= 0.75 ? 'warning' : 'normal'
  const percent = Math.round(ratio * 100)
  const openCard = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = null
    setOpen(true)
  }
  const scheduleClose = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null
      setOpen(false)
    }, CONTEXT_CLOSE_DELAY_MS)
  }
  const freePct = report !== null && report.window > 0 ? (report.free_tokens / report.window) * 100 : 0
  const totalLabel = `${report !== null && !report.measured ? '≈' : ''}${formatCompactTokens(
    report?.total_tokens ?? used,
  )}`
  return (
    <span
      className={css.contextMeterWrap}
      onPointerEnter={openCard}
      onPointerLeave={scheduleClose}
      onFocus={openCard}
      onBlur={scheduleClose}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false)
      }}
    >
      <span
        className={css.contextMeter}
        data-level={level}
        data-testid="context-meter"
        tabIndex={0}
        aria-describedby={open && report !== null ? cardId : undefined}
        title={report === null ? `${t('usage.context')} ${used} / ${window_}` : undefined}
      >
        <svg className={css.contextRing} viewBox="0 0 24 24" aria-hidden>
          <circle className={css.contextRingTrack} cx="12" cy="12" r={CONTEXT_RING_RADIUS} />
          <circle
            className={css.contextRingProgress}
            cx="12"
            cy="12"
            r={CONTEXT_RING_RADIUS}
            strokeDasharray={CONTEXT_RING_CIRCUMFERENCE}
            strokeDashoffset={CONTEXT_RING_CIRCUMFERENCE * (1 - ratio)}
          />
        </svg>
        <span className={css.contextValue}>{percent}%</span>
      </span>
      {open && report !== null && (
        <div className={css.contextCard} id={cardId} role="tooltip" data-testid="context-card">
          <div className={css.contextCardHead}>
            <strong className={css.contextCardHeadValue}>
              {t('context.spent', { tokens: totalLabel })}
            </strong>
            <strong className={css.contextCardHeadPercent}>{percent}%</strong>
          </div>
          <div className={css.contextCardWindow}>
            <span>{t('context.window')}</span>
            <strong>
              {formatCompactTokens(used)} / {formatCompactTokens(window_)}
            </strong>
          </div>
          <div className={css.contextCardRows}>
            {report.slices.map((slice) => (
              <div className={css.contextCardRow} key={slice.id} data-slice={slice.id}>
                <span className={css.contextCardLabel}>{t(`context.slice.${slice.id}`)}</span>
                <span className={css.contextCardValue}>
                  ≈{formatCompactTokens(slice.tokens)} · {slice.pct.toFixed(1)}%
                </span>
              </div>
            ))}
            <div className={clsx(css.contextCardRow, css.contextCardFree)} data-slice="free">
              <span className={css.contextCardLabel}>{t('context.free')}</span>
              <span className={css.contextCardValue}>
                ≈{formatCompactTokens(report.free_tokens)} · {freePct.toFixed(1)}%
              </span>
            </div>
          </div>
          <div className={css.contextCardNote}>
            {t(report.measured ? 'context.noteMeasured' : 'context.noteEstimated')}
          </div>
        </div>
      )}
    </span>
  )
}
