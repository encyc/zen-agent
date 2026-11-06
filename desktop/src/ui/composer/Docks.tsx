/** Composer docks: todo panel, pending lanes (queue/steer), context meter —
 * the dsh composer.dock entries, driven by the status snapshot. */

import { useState } from 'react'
import clsx from 'clsx'
import { useAppStore } from '../../state/appStore'
import { canDequeuePending, canPromotePending } from '../../state/pending'
import { useStatusStore } from '../../state/stores'
import type { PendingMessage } from '../../rpc/types'
import { useT } from '../../i18n/locale'
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
  const queued = pending.filter((item) => item.lane === 'queue')
  const steering = pending.filter((item) => item.lane === 'steer')
  const firstQueued = queued[0]
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
            firstQueued !== undefined && canPromotePending(firstQueued, running)
              ? () => void promotePending(firstQueued.id)
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

export function ContextMeter() {
  const status = useStatusStore((state) => state.status)
  const used = status?.context_used ?? null
  const window_ = status?.context_window ?? null
  if (used === null || window_ === null || window_ <= 0 || used <= 0) return null
  const ratio = Math.min(1, used / window_)
  return (
    <div className={css.contextMeter} title={`${used} / ${window_}`}>
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
        <circle cx="8" cy="8" r="6" fill="none" stroke="var(--dsw-alias-border-l2)" strokeWidth="2.5" />
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke={ratio > 0.85 ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-state-business-primary)'}
          strokeWidth="2.5"
          strokeDasharray={`${ratio * 37.7} 37.7`}
          strokeLinecap="round"
          transform="rotate(-90 8 8)"
        />
      </svg>
      <span>{Math.round(ratio * 100)}%</span>
    </div>
  )
}
