/** The swarm room: the global message feed, the composer that posts with
 * `swarm post`, and the roster/task side panel. Swarm frames are global, so
 * this page owns the whole main surface and reads only the swarm store. */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { SwarmAgentState, SwarmMessage, SwarmRole, SwarmTask } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { detectMention } from '../../state/mentions'
import { useSwarmStore } from '../../state/swarmStore'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import css from './RoomPage.module.css'

type TaskNumbers = ReadonlyMap<string, number>

export function RoomPage() {
  const t = useT()
  const messages = useSwarmStore((state) => state.messages)
  const tasks = useSwarmStore((state) => state.tasks)
  const agents = useSwarmStore((state) => state.agents)
  const roles = useSwarmStore((state) => state.roles)
  const warnings = useSwarmStore((state) => state.warnings)
  const loaded = useSwarmStore((state) => state.loaded)
  const load = useSwarmStore((state) => state.load)
  const feed = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!loaded) void load()
  }, [loaded, load])

  useEffect(() => {
    const node = feed.current
    if (node !== null) node.scrollTop = node.scrollHeight
  }, [messages.length])

  const numbers = useMemo(
    () => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])),
    [tasks],
  )

  return (
    <div className={css.root} data-swarm-page>
      <div className={css.main}>
        {warnings.length > 0 && (
          <section className={css.warnings} data-swarm-warnings role="status">
            <span className={css.warningsTitle}>
              {t('swarm.warnings', { n: warnings.length })}
            </span>
            <ul className={css.warningsList}>
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </section>
        )}
        <div
          ref={feed}
          className={css.feed}
          data-swarm-feed
          role="log"
          aria-label={t('swarm.feed')}
        >
          {messages.length === 0 ? (
            <div className={css.empty}>{t('swarm.empty')}</div>
          ) : (
            messages.map((message) => (
              <MessageRow key={message.id} message={message} roles={roles} numbers={numbers} />
            ))
          )}
        </div>
        <ApprovalPanel />
        <Composer roles={roles} />
      </div>
      <aside className={css.side}>
        <Roster agents={agents} numbers={numbers} />
        <TaskStrip tasks={tasks} numbers={numbers} />
      </aside>
    </div>
  )
}

function MessageRow({
  message,
  roles,
  numbers,
}: {
  message: SwarmMessage
  roles: SwarmRole[]
  numbers: TaskNumbers
}) {
  const t = useT()
  const kind = message.author.kind
  const name =
    kind === 'user'
      ? t('swarm.you')
      : kind === 'system'
        ? t('swarm.system')
        : (roles.find((role) => role.id === message.author.id)?.name ?? message.author.id)
  const number = message.taskId === null ? undefined : numbers.get(message.taskId)
  return (
    <div
      className={css.message}
      data-swarm-message
      data-message-id={message.id}
      data-author-kind={kind}
      data-own={kind === 'user' || undefined}
    >
      <div className={css.messageHead}>
        {kind !== 'system' && <span className={css.dot} data-kind={kind} aria-hidden />}
        <span className={css.author}>{name}</span>
        {number !== undefined && (
          <span className={css.pill} data-swarm-pill>
            {t('swarm.taskPill', { n: number })}
          </span>
        )}
      </div>
      <div className={css.text}>{message.text}</div>
      {message.mentions.length > 0 && (
        <div className={css.chips}>
          {message.mentions.map((mention) => (
            <span key={mention} className={css.chip}>
              @{mention}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function Composer({ roles }: { roles: SwarmRole[] }) {
  const t = useT()
  const post = useSwarmStore((state) => state.post)
  const [draft, setDraft] = useState('')
  const [caret, setCaret] = useState(0)
  const [active, setActive] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [busy, setBusy] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)

  const trigger = detectMention(draft, caret)
  const options =
    trigger === null
      ? []
      : roles.filter((role) =>
          `${role.id} ${role.name}`.toLowerCase().includes(trigger.query.toLowerCase()),
        )
  const open = !dismissed && trigger !== null && options.length > 0

  const update = (value: string, position: number) => {
    setDraft(value)
    setCaret(position)
    setActive(0)
    setDismissed(false)
  }

  const pick = (role: SwarmRole) => {
    if (trigger === null) return
    const next = `${draft.slice(0, trigger.tokenStart)}@${role.id} ${draft.slice(trigger.tokenEnd)}`
    const position = trigger.tokenStart + role.id.length + 2
    update(next, position)
    requestAnimationFrame(() => {
      const node = area.current
      if (node === null) return
      node.focus()
      node.setSelectionRange(position, position)
    })
  }

  const submit = async () => {
    const text = draft.trim()
    if (text === '' || busy) return
    setBusy(true)
    try {
      await post(text)
      update('', 0)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.composer} data-swarm-composer>
      {open && (
        <ul className={css.mentionMenu} data-swarm-mentions>
          {options.map((role, index) => (
            <li key={role.id}>
              <button
                type="button"
                className={css.mentionOption}
                data-swarm-mention={role.id}
                data-active={index === active || undefined}
                onMouseDown={(event) => {
                  event.preventDefault()
                  pick(role)
                }}
              >
                <span className={css.mentionId}>@{role.id}</span>
                <span className={css.mentionName}>{role.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={area}
        className={css.input}
        data-swarm-input
        rows={1}
        value={draft}
        placeholder={t('swarm.placeholder')}
        onChange={(event) => update(event.target.value, event.target.selectionStart ?? 0)}
        onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            if (open) pick(options[Math.min(active, options.length - 1)])
            else void submit()
            return
          }
          if (!open) return
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setActive((index) => (index + 1) % options.length)
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setActive((index) => (index - 1 + options.length) % options.length)
          } else if (event.key === 'Escape') {
            event.preventDefault()
            setDismissed(true)
          }
        }}
      />
      <button type="button" className={css.send} disabled={draft.trim() === '' || busy} onClick={() => void submit()}>
        {t('swarm.send')}
      </button>
    </div>
  )
}

function Roster({ agents, numbers }: { agents: SwarmAgentState[]; numbers: TaskNumbers }) {
  const t = useT()
  const reset = useSwarmStore((state) => state.reset)
  const reload = useSwarmStore((state) => state.reload)
  return (
    <section className={css.block}>
      <div className={css.blockHead}>
        <h2 className={css.title}>{t('swarm.agents')}</h2>
        <button
          type="button"
          className={css.ghost}
          data-swarm-reload
          onClick={() => void reload()}
        >
          {t('swarm.reload')}
        </button>
      </div>
      {agents.length === 0 ? (
        <p className={css.blockEmpty}>{t('swarm.noAgents')}</p>
      ) : (
        <ul className={css.rows} data-swarm-roster>
          {agents.map((agent) => {
            const number = agent.task === null ? undefined : numbers.get(agent.task)
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
                <button
                  type="button"
                  className={css.ghost}
                  data-swarm-reset
                  disabled={agent.state === 'running'}
                  onClick={() => void reset(agent.agent)}
                >
                  {t('swarm.reset')}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function TaskStrip({ tasks, numbers }: { tasks: SwarmTask[]; numbers: TaskNumbers }) {
  const t = useT()
  const cancel = useSwarmStore((state) => state.cancel)
  const retry = useSwarmStore((state) => state.retry)
  return (
    <section className={css.block}>
      <h2 className={css.title}>{t('swarm.tasks')}</h2>
      {tasks.length === 0 ? (
        <p className={css.blockEmpty}>{t('swarm.noTasks')}</p>
      ) : (
        <ul className={css.rows} data-swarm-tasks>
          {tasks.map((task) => (
            <li
              key={task.id}
              className={css.taskRow}
              data-swarm-task
              data-task-id={task.id}
              data-state={task.state}
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
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
