/** The full role manifest editor, shared by Settings → Agents and the swarm
 * room's roster. MCP servers arrive as raw manifest entries, so credentials
 * stay `${KEY}` placeholders in the form and are resolved only host-side. */

import { useEffect, useState, type FormEvent } from 'react'
import { useLocale, useT } from '../../i18n/locale'
import type { Locale } from '../../i18n/locale'
import { frameErrorNotice, type ReplyLike } from '../../rpc/frames'
import type { SwarmRole } from '../../rpc/types'
import css from './AgentSettings.module.css'

export const ROLE_ID = /^[a-z0-9][a-z0-9._-]*$/
const MODES = ['plan', 'ask', 'zen'] as const

export function roleNotice(
  reply: Record<string, unknown>,
  locale: Locale,
  fallback: string,
): string {
  const message = reply.message
  return (
    frameErrorNotice(reply as ReplyLike, locale) ??
    (typeof message === 'string' && message !== '' ? message : fallback)
  )
}

function splitList(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '')
}

function serversOf(row: SwarmRole | null): string {
  if (row === null || Object.keys(row.mcpServers).length === 0) return ''
  return JSON.stringify(row.mcpServers, null, 2)
}

export function AgentEditor(props: {
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
  const [prompt, setPrompt] = useState(row?.systemPrompt ?? '')
  const [skills, setSkills] = useState((row?.skills ?? []).join(', '))
  const [plugins, setPlugins] = useState((row?.plugins ?? []).join(', '))
  const [mcpText, setMcpText] = useState(serversOf(row))
  const [inheritProject, setInheritProject] = useState(row?.inheritProject ?? true)
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

    let mcpServers: Record<string, unknown> | null = null
    if (mcpText.trim() !== '') {
      try {
        const parsed: unknown = JSON.parse(mcpText)
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('not an object')
        }
        mcpServers = parsed as Record<string, unknown>
      } catch {
        setError(t('swarm.roleInvalidMcp'))
        return
      }
    }

    const limits: Record<string, number> = {}
    if (turns !== null) limits.maxTurns = turns
    if (cost !== null) limits.maxCost = cost
    const toolList = splitList(tools)
    // Nulls delete a field; omitted keys keep whatever the file already has.
    const manifest: Record<string, unknown> = {
      schemaVersion: 1,
      name: name.trim() || roleId,
      description: description.trim(),
      mode,
      model: model.trim() === '' ? null : model.trim(),
      thinking: thinking.trim() === '' ? null : thinking.trim(),
      systemPrompt: prompt.trim(),
      skills: splitList(skills),
      plugins: splitList(plugins),
      mcpServers,
      inheritProject,
      root: root.trim() === '' ? null : root.trim(),
      tools: toolList.length > 0 ? toolList : null,
      limits: Object.keys(limits).length > 0 ? limits : null,
    }

    setSaving(true)
    setError('')
    try {
      const answer = await props.onSave(row === null ? 'create' : 'update', roleId, manifest)
      if (answer.ok === true) {
        props.onClose()
      } else {
        setError(roleNotice(answer, locale, t('swarm.roleSaveFailed', { message: '' })))
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
          {row === null ? t('swarm.roleFormCreate') : t('swarm.roleFormEdit', { id: row.id })}
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

      <div className={css.grid2}>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-skills">
            {t('swarm.roleSkills')}
          </label>
          <input
            id="agent-skills"
            className={css.input}
            value={skills}
            spellCheck={false}
            onChange={(event) => setSkills(event.target.value)}
          />
          <div className={css.hint}>{t('swarm.roleSkillsHint')}</div>
        </div>
        <div className={css.field}>
          <label className={css.label} htmlFor="agent-plugins">
            {t('swarm.rolePlugins')}
          </label>
          <input
            id="agent-plugins"
            className={css.input}
            value={plugins}
            spellCheck={false}
            onChange={(event) => setPlugins(event.target.value)}
          />
          <div className={css.hint}>{t('swarm.rolePluginsHint')}</div>
        </div>
      </div>

      <div className={css.field}>
        <label className={css.label} htmlFor="agent-mcp">
          {t('swarm.roleMcp')}
        </label>
        <textarea
          id="agent-mcp"
          className={`${css.textarea} ${css.code}`}
          rows={5}
          value={mcpText}
          spellCheck={false}
          placeholder={t('swarm.roleMcpPlaceholder')}
          onChange={(event) => setMcpText(event.target.value)}
        />
        <div className={css.hint}>{t('swarm.roleMcpHint')}</div>
      </div>

      <label className={css.checkRow} htmlFor="agent-inherit-project">
        <input
          id="agent-inherit-project"
          type="checkbox"
          checked={inheritProject}
          onChange={(event) => setInheritProject(event.target.checked)}
        />
        <span>{t('swarm.roleInheritProject')}</span>
        <span className={css.hint}>{t('swarm.roleInheritProjectHint')}</span>
      </label>

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

/** The editor in a centered overlay, used by the room roster. */
export function AgentModal(props: {
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
  const close = props.onClose
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  return (
    <div
      className={css.backdrop}
      data-swarm-role-modal
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        className={css.modal}
        role="dialog"
        aria-modal="true"
        aria-label={
          props.row === null
            ? t('swarm.roleFormCreate')
            : t('swarm.roleFormEdit', { id: props.row.id })
        }
      >
        <AgentEditor
          row={props.row}
          busy={props.busy}
          onClose={close}
          onSave={props.onSave}
        />
      </div>
    </div>
  )
}
