/** Composer todo dock helpers: the status snapshot carries ordered phases,
 * each holding content-addressed tasks. The dock flattens them for progress
 * and keeps the phase name on every task for the expanded list. */

import type { TodoPhase } from '../../rpc/types'

export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'abandoned' | 'blocked'

const STATUSES = new Set<string>([
  'pending',
  'in_progress',
  'completed',
  'abandoned',
  'blocked',
])

const CLOSED = new Set<TodoStatus>(['completed', 'abandoned'])

export interface TodoTask {
  content: string
  status: TodoStatus
  blocker: string
  phase: string
}

export interface TodoProgress {
  /** Short label for the collapsed header, e.g. "2/4 completed". */
  label: string
  /** Glyph that carries the same state as the label. */
  symbol: string
  /** First in-progress task, shown beside the label while collapsed. */
  current: TodoTask | undefined
}

export function flattenTodos(phases: readonly TodoPhase[] | undefined): TodoTask[] {
  const tasks: TodoTask[] = []
  for (const phase of phases ?? []) {
    const name = typeof phase.name === 'string' ? phase.name.trim() : ''
    if (name === '') continue
    const rows = phase.tasks
    if (!Array.isArray(rows)) continue
    for (const row of rows) {
      if (row === null || typeof row !== 'object') continue
      const { content, status: rawStatus, blocker: rawBlocker } = row as {
        content?: unknown
        status?: unknown
        blocker?: unknown
      }
      if (typeof content !== 'string' || content.trim() === '') continue
      const status: TodoStatus =
        typeof rawStatus === 'string' && STATUSES.has(rawStatus)
          ? (rawStatus as TodoStatus)
          : 'pending'
      const blocker =
        status === 'blocked' && typeof rawBlocker === 'string' ? rawBlocker.trim() : ''
      tasks.push({ content: content.trim(), status, blocker, phase: name })
    }
  }
  return tasks
}

export function todoProgress(
  tasks: readonly TodoTask[],
  t: (key: string, params?: Record<string, string | number>) => string,
): TodoProgress {
  const completed = tasks.filter((task) => task.status === 'completed').length
  const abandoned = tasks.filter((task) => task.status === 'abandoned').length
  const denominator = tasks.length - abandoned
  const current = tasks.find((task) => task.status === 'in_progress')
  const allAbandoned = tasks.length > 0 && abandoned === tasks.length
  const finished = tasks.every((task) => CLOSED.has(task.status))
  return {
    label: allAbandoned
      ? t('chat.todo.status.abandoned')
      : finished
        ? t('chat.todo.completed', { completed, total: denominator })
        : t('chat.todo.progress', { completed, total: denominator }),
    symbol: allAbandoned ? '\u2298' : finished ? '\u2713' : '\u25d0',
    current,
  }
}
