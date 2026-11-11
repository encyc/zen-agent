/** Settings → Agents: the resident swarm roles, their status list and the
 * shared create/edit editor. The host owns manifest validation. */

import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useLocale, useT } from '../../i18n/locale'
import type { SwarmRole } from '../../rpc/types'
import { useAppStore } from '../../state/appStore'
import { useSwarmStore } from '../../state/swarmStore'
import { AgentEditor, roleNotice } from './agentEditor'
import css from './AgentSettings.module.css'

function statusOf(role: SwarmRole): 'ready' | 'needs_credentials' {
  return role.missingCredentials.length === 0 ? 'ready' : 'needs_credentials'
}

function RoleRow(props: {
  role: SwarmRole
  busy: boolean
  armed: boolean
  onEdit(): void
  onArm(): void
  onDelete(): void
}) {
  const t = useT()
  const { role } = props
  const status = statusOf(role)
  return (
    <div className={css.role} data-agent-role={role.id} data-status={status}>
      <div className={css.roleMain}>
        <div className={css.name}>
          <span>{role.name}</span>
          <span className={css.id}>{role.id}</span>
          <span className={css.status} data-status={status}>
            {status === 'ready'
              ? t('swarm.roleStatusReady')
              : t('swarm.roleStatusNeedsCredentials', {
                  keys: role.missingCredentials.join(', '),
                })}
          </span>
        </div>
        <div className={css.description}>{role.description}</div>
        <div className={css.meta}>
          <span>{role.model}</span>
          <span>{role.mode}</span>
          <span className={css.root}>{role.root}</span>
        </div>
      </div>
      <div className={css.actions}>
        <button type="button" className={css.action} disabled={props.busy} onClick={props.onEdit}>
          {t('swarm.roleEdit')}
        </button>
        <button
          type="button"
          className={clsx(css.action, props.armed && css.actionDanger)}
          disabled={props.busy}
          onClick={() => {
            if (!props.armed) {
              props.onArm()
              return
            }
            props.onDelete()
          }}
        >
          {props.armed ? t('swarm.roleDeleteArmed') : t('swarm.roleDelete')}
        </button>
      </div>
      {props.armed ? <div className={css.warning}>{t('swarm.roleDeleteWarning')}</div> : null}
    </div>
  )
}

export function AgentSettings() {
  const t = useT()
  const locale = useLocale()
  const project = useAppStore((state) => state.project)
  const roles = useSwarmStore((state) => state.roles)
  const load = useSwarmStore((state) => state.load)
  const saveRole = useSwarmStore((state) => state.saveRole)
  const [editing, setEditing] = useState<SwarmRole | 'new' | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [armed, setArmed] = useState<string | null>(null)
  const [error, setError] = useState('')
  const busy = pending !== null

  useEffect(() => {
    void load(project)
  }, [load, project])

  async function save(
    action: 'create' | 'update',
    id: string,
    manifest: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    setPending(`save:${id}`)
    setError('')
    try {
      const answer = await saveRole(action, id, manifest)
      if (answer.ok === true) {
        setEditing(null)
        await load(project)
      }
      return answer
    } catch (cause) {
      return { ok: false, message: cause instanceof Error ? cause.message : String(cause) }
    } finally {
      setPending(null)
    }
  }

  async function remove(id: string) {
    if (busy) return
    setPending(`delete:${id}`)
    setError('')
    setArmed(null)
    try {
      const answer = await saveRole('delete', id)
      if (answer.ok === true) await load(project)
      else setError(roleNotice(answer, locale, t('swarm.roleSaveFailed', { message: '' })))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

  const drawerRow = editing === 'new' ? null : editing

  return (
    <div className={css.stack}>
      {editing !== null ? (
        <AgentEditor
          key={drawerRow === null ? 'new' : drawerRow.id}
          row={drawerRow}
          busy={busy}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      ) : null}

      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div className={css.headCopy}>
            <h2 className={css.cardHeading}>{t('swarm.rolesList')}</h2>
            <p className={css.cardCaption}>{t('settings.agentsHint')}</p>
          </div>
          <button
            type="button"
            className={css.primary}
            disabled={busy}
            onClick={() => setEditing('new')}
          >
            {t('swarm.roleNew')}
          </button>
        </div>

        <div className={css.panel}>
          {roles.length === 0 ? (
            <div className={css.empty}>{t('swarm.rolesEmpty')}</div>
          ) : (
            roles.map((role) => (
              <RoleRow
                key={role.id}
                role={role}
                busy={busy}
                armed={armed === role.id}
                onEdit={() => setEditing(role)}
                onArm={() => setArmed(role.id)}
                onDelete={() => void remove(role.id)}
              />
            ))
          )}
        </div>
      </section>

      {error ? <div className={css.error}>{error}</div> : null}
    </div>
  )
}
