/** The swarm room: the global message feed, the composer that posts with
 * `swarm post`, and the roster/task side panel. Swarm frames are global, so
 * this page owns the whole main surface and reads only the swarm store. */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type {
  SwarmAgentState,
  SwarmMessage,
  SwarmRole,
  SwarmTask,
} from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { useAppStore } from '../../state/appStore'
import { detectMention } from '../../state/mentions'
import { nodesFromViews, type ChatNode } from '../../state/chatStore'
import type { RunView } from '../../state/swarmRun'
import { useSwarmStore } from '../../state/swarmStore'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import { AssistantNodeView } from '../chat/AssistantNodeView'
import { SkillMessage, UserMessage } from '../chat/MessageItem'
import { ToolRowView } from '../chat/ToolRowView'
import { AgentModal } from './agentEditor'
import css from './RoomPage.module.css'

type TaskNumbers = ReadonlyMap<string, number>

/** Follow the tail while the reader is within this many px of the bottom
 * (the same stickiness the subagent transcript uses). */
const STICK_PX = 80
const NO_ROWS: RunView[] = []

export function RoomPage() {
  const t = useT()
  const project = useAppStore((state) => state.project)
  const messages = useSwarmStore((state) => state.messages)
  const tasks = useSwarmStore((state) => state.tasks)
  const agents = useSwarmStore((state) => state.agents)
  const roles = useSwarmStore((state) => state.roles)
  const warnings = useSwarmStore((state) => state.warnings)
  const loaded = useSwarmStore((state) => state.loaded)
  const load = useSwarmStore((state) => state.load)
  const saveRole = useSwarmStore((state) => state.saveRole)
  const [editing, setEditing] = useState<SwarmRole | 'new' | null>(null)
  const [selectedTask, setSelectedTask] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const feed = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!loaded) void load(project)
  }, [loaded, load, project])

  useEffect(() => {
    const node = feed.current
    if (node !== null) node.scrollTop = node.scrollHeight
  }, [messages.length, selectedTask])

  const numbers = useMemo(
    () => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])),
    [tasks],
  )
  const detail = tasks.find((task) => task.id === selectedTask) ?? null

  async function save(
    action: 'create' | 'update',
    id: string,
    manifest: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    setBusy(true)
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
      setBusy(false)
    }
  }

  return (
    <div className={css.root} data-swarm-page>
      <div className={css.main} data-swarm-main>
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
        {detail !== null ? (
          <TaskDetailPanel
            task={detail}
            messages={messages}
            numbers={numbers}
            onClose={() => setSelectedTask(null)}
          />
        ) : (
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
        )}
        <ApprovalPanel />
        <Composer roles={roles} />
      </div>
      <aside className={css.side}>
        <Roster
          agents={agents}
          roles={roles}
          numbers={numbers}
          busy={busy}
          onConfigure={setEditing}
          onCreate={() => setEditing('new')}
        />
        <TaskStrip
          tasks={tasks}
          numbers={numbers}
          selectedId={selectedTask}
          onSelect={(id) => setSelectedTask((current) => (current === id ? null : id))}
        />
      </aside>
      {editing !== null && (
        <AgentModal
          row={editing === 'new' ? null : editing}
          busy={busy}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      )}
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

function Roster({
  agents,
  roles,
  numbers,
  busy,
  onConfigure,
  onCreate,
}: {
  agents: SwarmAgentState[]
  roles: SwarmRole[]
  numbers: TaskNumbers
  busy: boolean
  onConfigure(role: SwarmRole): void
  onCreate(): void
}) {
  const t = useT()
  const reset = useSwarmStore((state) => state.reset)
  const reload = useSwarmStore((state) => state.reload)
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

function TaskStrip({
  tasks,
  numbers,
  selectedId,
  onSelect,
}: {
  tasks: SwarmTask[]
  numbers: TaskNumbers
  selectedId: string | null
  onSelect(id: string): void
}) {
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
                    onClick={() => onSelect(task.id)}
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

/** The selected task opened in the conversation column: a pinned card above
 * the feed, so the task list stays a compact index. */
function TaskDetailPanel({
  task,
  messages,
  numbers,
  onClose,
}: {
  task: SwarmTask
  messages: SwarmMessage[]
  numbers: TaskNumbers
  onClose(): void
}) {
  const t = useT()
  const source = messages.find((message) => message.id === task.sourceMessageId)
  const fetchTranscript = useSwarmStore((state) => state.transcript)
  const active = task.state === 'running' || task.state === 'queued'
  const liveRows = useSwarmStore((state) => state.runRows[task.id])
  const [steps, setSteps] = useState<RunView[] | null>(null)
  const [recorded, setRecorded] = useState<boolean | null>(null)
  // The store releases live rows when the task settles; keep the last tail
  // so a failed transcript fetch never blanks what the reader just watched.
  const tail = useRef<RunView[]>(NO_ROWS)
  if (liveRows !== undefined) tail.current = liveRows
  const rows = steps ?? liveRows ?? tail.current
  const runNodes = useMemo(() => nodesFromViews(rows), [rows])
  const scrollRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)

  useEffect(() => {
    setSteps(null)
    setRecorded(null)
    tail.current = NO_ROWS
    stick.current = true
  }, [task.id])

  // The role's session file only lands when its run ends. Once the task
  // settles, fetch it once — it is the authoritative record and reconciles
  // anything missed mid-stream.
  useEffect(() => {
    if (active) return
    let alive = true
    const refresh = async (): Promise<void> => {
      const answer = await fetchTranscript(task.id).catch(() => null)
      if (!alive) return
      if (answer === null) {
        setRecorded(false)
        return
      }
      setSteps(answer.transcript)
      setRecorded(answer.available)
    }
    void refresh()
    return () => {
      alive = false
    }
  }, [task.id, active, fetchTranscript])

  useLayoutEffect(() => {
    const element = scrollRef.current
    if (element === null || !stick.current) return
    element.scrollTop = element.scrollHeight
  }, [runNodes])

  const onScroll = () => {
    const element = scrollRef.current
    if (element === null) return
    stick.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < STICK_PX
  }

  const author =
    task.createdBy.kind === 'user'
      ? t('swarm.you')
      : task.createdBy.kind === 'system'
        ? t('swarm.system')
        : `@${task.createdBy.id}`
  const retryOf =
    task.derivedFrom === null
      ? null
      : numbers.has(task.derivedFrom)
        ? `#${numbers.get(task.derivedFrom)}`
        : task.derivedFrom
  return (
    <div
      className={css.detail}
      data-swarm-task-detail-scroll
      ref={scrollRef}
      onScroll={onScroll}
    >
      <section className={css.taskPanel} data-swarm-task-detail data-task-id={task.id}>
      <header className={css.taskPanelHead}>
        <span className={css.taskPanelTitle}>
          {t('swarm.taskLabel', { n: numbers.get(task.id) ?? 0 })}
        </span>
        <span className={css.rowCaption}>@{task.assignee}</span>
        <span className={css.taskState} data-state={task.state}>
          {t(`swarm.task.${task.state}`)}
        </span>
        <button
          type="button"
          className={`${css.ghost} ${css.taskPanelClose}`}
          data-swarm-task-detail-close
          onClick={onClose}
        >
          {t('swarm.taskDetailClose')}
        </button>
      </header>
      <dl className={css.taskDetail}>
        <TaskDetailRow label={t('swarm.taskDetailId')} value={task.id} />
        <TaskDetailRow label={t('swarm.taskDetailBy')} value={author} />
        <TaskDetailRow
          label={t('swarm.taskDetailChain')}
          value={t('swarm.taskDetailChainValue', {
            depth: task.chainDepth,
            cost: task.chainCost.toFixed(2),
          })}
        />
        {task.reason !== null && (
          <TaskDetailRow label={t('swarm.taskDetailReason')} value={task.reason} />
        )}
        {retryOf !== null && (
          <TaskDetailRow label={t('swarm.taskDetailRetryOf')} value={retryOf} />
        )}
      </dl>
      {source !== undefined && (
        <figure className={css.taskQuote}>
          <figcaption className={css.taskQuoteLabel}>{t('swarm.taskDetailSource')}</figcaption>
          <blockquote className={css.taskQuoteText}>{source.text}</blockquote>
        </figure>
      )}
      <section className={css.taskRun}>
        <h3 className={css.taskRunTitle}>{t('swarm.execution')}</h3>
        {runNodes.length === 0 ? (
          <p className={css.blockEmpty}>
            {recorded === false && !active
              ? t('swarm.executionUnavailable')
              : t('swarm.executionEmpty')}
          </p>
        ) : (
          <div className={css.taskRunBody} data-swarm-task-steps>
            {runNodes.map((node) => (
              <TaskRunRow key={node.id} node={node} />
            ))}
          </div>
        )}
      </section>
      </section>
    </div>
  )
}

function TaskDetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className={css.taskDetailRow}>
      <dt className={css.taskDetailKey}>{label}</dt>
      <dd className={css.taskDetailValue}>{value}</dd>
    </div>
  )
}

/** One row of a task's run: the same components the coding transcript uses,
 * so tool cards, markdown and reasoning render identically. */
function TaskRunRow({ node }: { node: ChatNode }) {
  switch (node.kind) {
    case 'user':
      return <UserMessage text={node.text} steering={node.steering} />
    case 'assistant':
      return <AssistantNodeView node={node} />
    case 'tool':
      return <ToolRowView node={node} />
    case 'skill':
      return <SkillMessage name={node.name} args={node.args} body={node.body} />
    default:
      return null
  }
}
