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
  onCopy(): void
}) {
  const t = useT()
  const agents = useSwarmStore((state) => state.agents)
  const roles = useSwarmStore((state) => state.roles)
  const tasks = useSwarmStore((state) => state.tasks)
  const resetAgent = useSwarmStore((state) => state.resetAgent)
  const reload = useSwarmStore((state) => state.reload)
  const numbers = useMemo(
    () => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])),
    [tasks],
  )
  const byId = useMemo(
    () => new Map(agents.map((agent) => [agent.agent, agent])),
    [agents],
  )
  const { busy, onConfigure, onCreate, onCopy } = props
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
            data-swarm-copy-role
            disabled={busy}
            onClick={onCopy}
          >
            {t('swarm.copyFromLibrary')}
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
      {roles.length === 0 ? (
        <p className={css.blockEmpty}>{t('swarm.noAgents')}</p>
      ) : (
        <ul className={css.rows} data-swarm-roster>
          {roles.map((role) => {
            const agent = byId.get(role.id)
            const state = agent?.state ?? 'idle'
            const number =
              agent?.task === null || agent?.task === undefined
                ? undefined
                : numbers.get(agent.task)
            return (
              <li
                key={role.id}
                className={css.agentRow}
                data-swarm-agent={role.id}
                data-state={state}
              >
                <span className={css.stateDot} data-state={state} aria-hidden />
                <div className={css.rowMain}>
                  <span className={css.rowTitle}>{role.name}</span>
                  <span className={css.rowCaption}>
                    {t(`swarm.agent.${state}`)}
                    {number !== undefined ? ` · #${number}` : ''}
                  </span>
                </div>
                {agent?.pendingApply && <span className={css.badge}>{t('swarm.pendingApply')}</span>}
                <div className={css.rowActions}>
                  <button
                    type="button"
                    className={css.ghost}
                    data-swarm-configure={role.id}
                    disabled={busy}
                    onClick={() => onConfigure(role)}
                  >
                    {t('swarm.roleConfigure')}
                  </button>
                  <button
                    type="button"
                    className={css.ghost}
                    data-swarm-reset
                    disabled={busy || state === 'running'}
                    onClick={() => void resetAgent(role.id)}
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