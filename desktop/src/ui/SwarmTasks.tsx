/** The swarm task index, moved from the room page's side panel into the
 * swarm sidebar: a compact list whose toggle opens the detail in the room. */

import { useMemo } from 'react'
import { useT } from '../i18n/locale'
import { useSwarmStore } from '../state/swarmStore'
import css from './SwarmPanels.module.css'

export function SwarmTasks() {
  const t = useT()
  const tasks = useSwarmStore((state) => state.tasks)
  const selectedId = useSwarmStore((state) => state.selectedTask)
  const selectTask = useSwarmStore((state) => state.selectTask)
  const cancel = useSwarmStore((state) => state.cancel)
  const retry = useSwarmStore((state) => state.retry)
  const numbers = useMemo(
    () => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])),
    [tasks],
  )
  return (
    <section className={css.block}>
      <h2 className={css.title}>{t('swarm.tasks')}</h2>
      {tasks.length === 0 ? (
        <p className={css.blockEmpty}>{t('swarm.noTasks')}</p>
      ) : (
        <ul className={css.rows} data-swarm-tasks>
          {tasks.map((task) => {
            const selected = selectedId === task.id
            return (
              <li
                key={task.id}
                className={css.taskItem}
                data-swarm-task
                data-task-id={task.id}
                data-state={task.state}
                data-selected={selected || undefined}
              >
                <div className={css.taskRow}>
                  <button
                    type="button"
                    className={css.taskToggle}
                    data-swarm-task-toggle
                    aria-expanded={selected}
                    onClick={() => selectTask(task.id)}
                  >
                    <div className={css.rowMain}>
                      <span className={css.rowTitle}>
                        {t('swarm.taskLabel', { n: numbers.get(task.id) ?? 0 })}
                      </span>
                      <span className={css.rowCaption}>@{task.assignee}</span>
                    </div>
                    <span className={css.taskState} data-state={task.state}>
                      {t(`swarm.task.${task.state}`)}
                    </span>
                  </button>
                  {(task.state === 'queued' || task.state === 'running') && (
                    <button
                      type="button"
                      className={css.ghost}
                      data-swarm-cancel
                      onClick={() => void cancel(task.id)}
                    >
                      {t('swarm.cancel')}
                    </button>
                  )}
                  {(task.state === 'failed' || task.state === 'cancelled') && (
                    <button
                      type="button"
                      className={css.ghost}
                      data-swarm-retry
                      onClick={() => void retry(task.id)}
                    >
                      {t('swarm.retry')}
                    </button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}