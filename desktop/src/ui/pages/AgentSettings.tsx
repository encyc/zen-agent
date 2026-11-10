/** Settings → Agents: the resident swarm roles and their create/edit form.
 * The host owns manifest validation; this panel only refuses ids the host
 * would reject and sends the same field names the manifest schema reads. */

import { useEffect, useState, type FormEvent } from 'react'
import clsx from 'clsx'
import { useLocale, useT } from '../../i18n/locale'
import type { Locale } from '../../i18n/locale'
import { frameErrorNotice, type ReplyLike } from '../../rpc/frames'
import type { SwarmRole } from '../../rpc/types'
import { useSwarmStore } from '../../state/swarmStore'
import css from './AgentSettings.module.css'

const ROLE_ID = /^[a-z0-9][a-z0-9._-]*$/
const MODES = ['plan', 'ask', 'zen'] as const

function noticeOf(reply: Record<string, unknown>, locale: Locale, fallback: string): string {
  const message = reply.message
  return (
    frameErrorNotice(reply as ReplyLike, locale) ??
    (typeof message === 'string' && message !== '' ? message : fallback)
  )
}

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

function RoleDrawer(props: {
  row: SwarmRole | null
  busy: boolean
  onClose(): void
  onSave(
    action: 'create' | 'update',
    id: string,
    manifest: Record<string, unknown>,
  ): Promise<Record<string, unknown>>
}) {
  const t = useT()
  const locale = useLocale()
  const row = props.row
  const [id, setId] = useState(row?.id ?? '')
  const [name, setName] = useState(row?.name ?? '')
  const [description, setDescription] = useState(row?.description ?? '')
  const [model, setModel] = useState(row?.model ?? '')
  const [thinking, setThinking] = useState(row?.thinking ?? '')
  const [prompt, setPrompt] = useState('')
  const [tools, setTools] = useState(row?.tools?.join(', ') ?? '')
  const [root, setRoot] = useState(row?.root ?? '')
  const [mode, setMode] = useState<string>(row?.mode ?? 'ask')
  const [maxTurns, setMaxTurns] = useState(row?.maxTurns === null ? '' : String(row?.maxTurns ?? ''))
  const [maxCost, setMaxCost] = useState(row?.maxCost === null ? '' : String(row?.maxCost ?? ''))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function save(event: FormEvent) {
    event.preventDefault()
    if (saving) return
    const roleId = id.trim()
    if (!ROLE_ID.test(roleId)) {
      setError(t('swarm.roleInvalidId'))
      return
    }
    const turns = maxTurns.trim() === '' ? null : Number(maxTurns)
    const cost = maxCost.trim() === '' ? null : Number(maxCost)
    if (
      (turns !== null && (!Number.isInteger(turns) || turns <= 0)) ||
      (cost !== null && (!Number.isFinite(cost) || cost < 0))
    ) {
      setError(t('swarm.roleInvalidNumber'))
      return
    }

    const manifest: Record<string, unknown> = {
      schemaVersion: 1,
      name: name.trim() || roleId,
      description: description.trim(),
      mode,
      inheritProject: false,
    }
    if (model.trim() !== '') manifest.model = model.trim()
    if (thinking.trim() !== '') manifest.thinking = thinking.trim()
    if (prompt.trim() !== '') manifest.systemPrompt = prompt.trim()
    if (root.trim() !== '') manifest.root = root.trim()
    const toolList = tools
      .split(',')
      .map((entry) => entry.trim())
      .filter((entry) => entry !== '')
    if (toolList.length > 0) manifest.tools = toolList
    const limits: Record<string, number> = {}
    if (turns !== null) limits.maxTurns = turns
    if (cost !== null) limits.maxCost = cost
    if (Object.keys(limits).length > 0) manifest.limits = limits

    setSaving(true)
    setError('')
    try {
      const answer = await props.onSave(row === null ? 'create' : 'update', roleId, manifest)
      if (answer.ok === true) {
        props.onClose()
      } else {
        setError(noticeOf(answer, locale, t('swarm.roleSaveFailed', { message: '' })))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className={css.drawer} onSubmit={save} data-agent-drawer>
      <div className={css.drawerHead}>
        <h3 className={css.drawerTitle}>
          {row === null
            ? t('swarm.roleFormCreate')
            : t('swarm.roleFormEdit', { id: row.id })}
        </h3>
        <button type="button" className={css.action} onClick={props.onClose}>
          {t('common.cancel')}
        </button>
      </div>
      <p className={css.hint}>{t('swarm.roleFormHint')}</p>

      <div className={css.grid2}>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-id">
            {t('swarm.roleId')}
          </label>
          <input
            id="agent-id"
            className={css.input}
            value={id}
            disabled={row !== null}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setId(event.target.value)}
          />
          <div className={css.hint}>{t('swarm.roleIdHint')}</div>
        </div>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-name">
            {t('swarm.roleName')}
          </label>
          <input
            id="agent-name"
            className={css.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
      </div>

      <div className={css.field}>
        <label className={css.label} htmlFor="agent-description">
          {t('swarm.roleDescription')}
        </label>
        <input
          id="agent-description"
          className={css.input}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>

      <div className={css.grid2}>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-model">
            {t('swarm.roleModel')}
          </label>
          <input
            id="agent-model"
            className={css.input}
            value={model}
            spellCheck={false}
            onChange={(event) => setModel(event.target.value)}
          />
        </div>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-thinking">
            {t('swarm.roleThinking')}
          </label>
          <input
            id="agent-thinking"
            className={css.input}
            value={thinking}
            spellCheck={false}
            onChange={(event) => setThinking(event.target.value)}
          />
        </div>
      </div>

      <div className={css.field}>
        <label className={css.label} htmlFor="agent-prompt">
          {t('swarm.rolePrompt')}
        </label>
        <textarea
          id="agent-prompt"
          className={css.textarea}
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </div>

      <div className={css.field}>
        <label className={css.label} htmlFor="agent-tools">
          {t('swarm.roleTools')}
        </label>
        <input
          id="agent-tools"
          className={css.input}
          value={tools}
          spellCheck={false}
          onChange={(event) => setTools(event.target.value)}
        />
        <div className={css.hint}>{t('swarm.roleToolsHint')}</div>
      </div>

      <div className={css.field}>
        <label className={css.label} htmlFor="agent-root">
          {t('swarm.roleRoot')}
        </label>
        <input
          id="agent-root"
          className={css.input}
          value={root}
          spellCheck={false}
          onChange={(event) => setRoot(event.target.value)}
        />
        <div className={css.hint}>{t('swarm.roleRootHint')}</div>
      </div>

      <div className={css.field}>
        <span className={css.label}>{t('swarm.roleMode')}</span>
        <div className={css.modeRow} role="group" aria-label={t('swarm.roleMode')}>
          {MODES.map((entry) => (
            <button
              key={entry}
              type="button"
              className={css.modeButton}
              aria-pressed={entry === mode}
              onClick={() => setMode(entry)}
            >
              {t(`mode.${entry}`)}
            </button>
          ))}
        </div>
      </div>

      <div className={css.grid2}>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-max-turns">
            {t('swarm.roleMaxTurns')}
          </label>
          <input
            id="agent-max-turns"
            className={css.input}
            inputMode="numeric"
            value={maxTurns}
            onChange={(event) => setMaxTurns(event.target.value)}
          />
        </div>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-max-cost">
            {t('swarm.roleMaxCost')}
          </label>
          <input
            id="agent-max-cost"
            className={css.input}
            inputMode="decimal"
            value={maxCost}
            onChange={(event) => setMaxCost(event.target.value)}
          />
        </div>
      </div>

      {error ? <div className={css.error}>{error}</div> : null}
      <div className={css.formActions}>
        <button type="submit" className={css.primary} disabled={saving || props.busy}>
          {t('swarm.roleSave')}
        </button>
      </div>
    </form>
  )
}

export function AgentSettings() {
  const t = useT()
  const locale = useLocale()
  const roles = useSwarmStore((state) => state.roles)
  const load = useSwarmStore((state) => state.load)
  const saveRole = useSwarmStore((state) => state.saveRole)
  const [editing, setEditing] = useState<SwarmRole | 'new' | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [armed, setArmed] = useState<string | null>(null)
  const [error, setError] = useState('')
  const busy = pending !== null

  useEffect(() => {
    void load()
  }, [load])

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
        await load()
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
      if (answer.ok === true) await load()
      else setError(noticeOf(answer, locale, t('swarm.roleSaveFailed', { message: '' })))
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
        <RoleDrawer
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
