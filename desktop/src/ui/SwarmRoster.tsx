/** The swarm roster, moved from the room page's side panel into the swarm
 * sidebar: one row per role agent with configure/reset actions. */

import { useMemo } from 'react'
import type { SwarmRole } from '../rpc/types'
import { useT } from '../i18n/locale'
import { useSwarmStore } from '../state/swarmStore'
import css from './SwarmPanels.module.css'

export function SwarmRoster(props: {
  busy: boolean
  onConfigure(role: SwarmRole): void
  onCreate(): void
}) {
  const t = useT()
  const agents = useSwarmStore((state) => state.agents)
  const roles = useSwarmStore((state) => state.roles)
  const tasks = useSwarmStore((state) => state.tasks)
  const reset = useSwarmStore((state) => state.reset)
  const reload = useSwarmStore((state) => state.reload)
  const numbers = useMemo(
    () => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])),
    [tasks],
  )
  const { busy, onConfigure, onCreate } = props
  return (
    <section className={css.block}>
      <div className={css.blockHead}>
        <h2 className={css.title}>{t('swarm.agents')}</h2>
        <div className={css.rosterActions}>
          <button
            type="button"
            className={css.ghost}
            data-swarm-new-role
            disabled={busy}
            onClick={onCreate}
          >
            {t('swarm.roleNew')}
          </button>
          <button
            type="button"
            className={css.ghost}
            data-swarm-reload
            onClick={() => void reload()}
          >
            {t('swarm.reload')}
          </button>
        </div>
      </div>
      {agents.length === 0 ? (
        <p className={css.blockEmpty}>{t('swarm.noAgents')}</p>
      ) : (
        <ul className={css.rows} data-swarm-roster>
          {agents.map((agent) => {
            const number = agent.task === null ? undefined : numbers.get(agent.task)
            const role = roles.find((entry) => entry.id === agent.agent)
            return (
              <li
                key={agent.agent}
                className={css.agentRow}
                data-swarm-agent={agent.agent}
                data-state={agent.state}
              >
                <span className={css.stateDot} data-state={agent.state} aria-hidden />
                <div className={css.rowMain}>
                  <span className={css.rowTitle}>{agent.name}</span>
                  <span className={css.rowCaption}>
                    {t(`swarm.agent.${agent.state}`)}
                    {number !== undefined ? ` · #${number}` : ''}
                  </span>
                </div>
                {agent.pendingApply && <span className={css.badge}>{t('swarm.pendingApply')}</span>}
                <div className={css.rowActions}>
                  <button
                    type="button"
                    className={css.ghost}
                    data-swarm-configure={agent.agent}
                    disabled={busy || role === undefined}
                    onClick={() => {
                      if (role !== undefined) onConfigure(role)
                    }}
                  >
                    {t('swarm.roleConfigure')}
                  </button>
                  <button
                    type="button"
                    className={css.ghost}
                    data-swarm-reset
                    disabled={agent.state === 'running'}
                    onClick={() => void reset(agent.agent)}
                  >
                    {t('swarm.reset')}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}