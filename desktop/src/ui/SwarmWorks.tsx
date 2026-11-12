/** The work item panel: list, rounds, and the human's score/close actions.
 *
 * Ad-hoc rounds (tasks without a work item) are shown separately and have no
 * score controls: they predate the concept and never enter the KPI. The panel
 * reads the room store's tasks for rounds, so an archived item with no live
 * runtime still shows its history. */

import { useEffect } from 'react'
import { useT } from '../i18n/locale'
import { useAppStore } from '../state/appStore'
import { useSwarmStore } from '../state/swarmStore'
import {
  isScorable,
  roundsOf,
  scoreText,
  sortWorks,
  workBadgeKey,
} from '../state/swarmWorks'
import panels from './SwarmPanels.module.css'
import css from './SwarmWorks.module.css'

const SCORES = [1, 2, 3, 4, 5] as const

export function SwarmWorks() {
  const t = useT()
  const project = useAppStore((state) => state.project)
  const works = useSwarmStore((state) => state.works)
  const adhoc = useSwarmStore((state) => state.adhoc)
  const tasks = useSwarmStore((state) => state.tasks)
  const selectedWork = useSwarmStore((state) => state.selectedWork)
  const selectWork = useSwarmStore((state) => state.selectWork)
  const scoreWork = useSwarmStore((state) => state.scoreWork)
  const closeWork = useSwarmStore((state) => state.closeWork)
  const reopenWork = useSwarmStore((state) => state.reopenWork)

  useEffect(() => {
    void useSwarmStore.getState().loadWorks(project)
  }, [project])

  const ordered = sortWorks(works)
  const current = ordered.find((work) => work.id === selectedWork) ?? null
  const rounds = current === null ? [] : roundsOf(tasks, current.id)

  return (
    <div className={css.works} data-swarm-works>
      <section className={panels.block}>
        <div className={panels.blockHead}>
          <h2 className={panels.title}>{t('swarm.works')}</h2>
          <span className={panels.rowCaption}>{works.length}</span>
        </div>
        {ordered.length === 0 ? (
          <p className={panels.blockEmpty}>{t('swarm.noWorks')}</p>
        ) : (
          <ul className={panels.rows} data-swarm-work-list>
            {ordered.map((work) => {
              const selected = work.id === selectedWork
              return (
                <li
                  key={work.id}
                  className={css.workItem}
                  data-selected={selected || undefined}
                  data-swarm-work={work.id}
                  data-state={work.state}
                >
                  <button
                    type="button"
                    className={css.workToggle}
                    aria-expanded={selected}
                    onClick={() => selectWork(work.id)}
                  >
                    <span className={panels.rowTitle}>
                      {work.title || work.id}
                    </span>
                    <span className={panels.rowCaption}>
                      {t('swarm.workRounds', { n: roundsOf(tasks, work.id).length })}
                      {' · '}
                      {scoreText(work) === null
                        ? t('swarm.workUnrated')
                        : `★ ${scoreText(work)}`}
                    </span>
                    <span className={css.badge} data-state={work.state}>
                      {t(workBadgeKey(work.state))}
                    </span>
                  </button>
                  {selected && (
                    <div className={css.detail} data-swarm-work-detail>
                      {isScorable(work) ? (
                        <div className={css.scoreRow} data-swarm-work-scores>
                          {SCORES.map((score) => (
                            <button
                              key={score}
                              type="button"
                              className={css.score}
                              data-swarm-work-score={score}
                              data-active={work.score === score || undefined}
                              onClick={() => void scoreWork(work.id, score)}
                            >
                              {score}
                            </button>
                          ))}
                          <button
                            type="button"
                            className={panels.ghost}
                            data-swarm-work-close
                            onClick={() => void closeWork(work.id, work.score)}
                          >
                            {t('swarm.workClose')}
                          </button>
                          {work.state === 'closed' && (
                            <button
                              type="button"
                              className={panels.ghost}
                              data-swarm-work-reopen
                              onClick={() => void reopenWork(work.id)}
                            >
                              {t('swarm.workReopen')}
                            </button>
                          )}
                        </div>
                      ) : (
                        <p className={css.hint}>{t('swarm.workMerged')}</p>
                      )}
                      <ul className={css.rounds} data-swarm-work-rounds>
                        {rounds.length === 0 ? (
                          <li className={css.hint}>{t('swarm.workRoundsEmpty')}</li>
                        ) : (
                          rounds.map((task, index) => (
                            <li
                              key={task.id}
                              className={css.round}
                              data-swarm-work-round
                              data-state={task.state}
                            >
                              <span className={css.roundTitle}>
                                #{index + 1} @{task.assignee}
                              </span>
                              <span className={css.roundState} data-state={task.state}>
                                {t(`swarm.task.${task.state}`)}
                              </span>
                            </li>
                          ))
                        )}
                      </ul>
                      <p className={css.hint}>{t('swarm.workScoreHint')}</p>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section className={panels.block} data-swarm-adhoc>
        <h2 className={panels.title}>{t('swarm.adhoc')}</h2>
        <p className={css.hint}>{t('swarm.adhocHint')}</p>
        {adhoc.length === 0 ? (
          <p className={panels.blockEmpty}>{t('swarm.adhocEmpty')}</p>
        ) : (
          <ul className={panels.rows} data-swarm-adhoc-list>
            {adhoc.map((task) => (
              <li key={task.id} className={css.adhocRow} data-state={task.state}>
                <span className={css.roundTitle}>
                  {task.id} @{task.assignee}
                </span>
                <span className={css.roundState} data-state={task.state}>
                  {t(`swarm.task.${task.state}`)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
