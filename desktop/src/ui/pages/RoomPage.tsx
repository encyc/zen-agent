/** The swarm room: the global message feed, the shared composer that posts
 * with `swarm post`, and the selected task's detail. The roster and task index
 * live in the swarm sidebar now; this page owns only the conversation column
 * and reads the swarm store. The sidebar owns the project-scoped load. */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { SwarmMessage, SwarmRole, SwarmTask } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { nodesFromViews, type ChatNode } from '../../state/chatStore'
import { useComposerStore } from '../../state/stores'
import type { RunView } from '../../state/swarmRun'
import { useSwarmStore } from '../../state/swarmStore'
import { RoomComposer } from '../composer/RoomComposer'
import { NoticeBanner } from '../NoticeBanner'
import { AssistantNodeView } from '../chat/AssistantNodeView'
import { SkillMessage, UserMessage } from '../chat/MessageItem'
import { ToolRowView } from '../chat/ToolRowView'
import css from './RoomPage.module.css'
import panels from '../SwarmPanels.module.css'

type TaskNumbers = ReadonlyMap<string, number>

/** Follow the tail while the reader is within this many px of the bottom
 * (the same stickiness the subagent transcript uses). */
const STICK_PX = 80
const NO_ROWS: RunView[] = []

export function RoomPage() {
  const t = useT()
  const messages = useSwarmStore((state) => state.messages)
  const tasks = useSwarmStore((state) => state.tasks)
  const roles = useSwarmStore((state) => state.roles)
  const warnings = useSwarmStore((state) => state.warnings)
  const selectedTask = useSwarmStore((state) => state.selectedTask)
  const selectTask = useSwarmStore((state) => state.selectTask)
  const project = useSwarmStore((state) => state.loadedProject) ?? ''
  const setQuote = useComposerStore((state) => state.setRoomQuote)
  const [flash, setFlash] = useState<string | null>(null)
  const feed = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = feed.current
    if (node !== null) node.scrollTop = node.scrollHeight
  }, [messages.length, selectedTask])

  const jumpTo = (id: string) => {
    const node = feed.current?.querySelector<HTMLElement>(`[data-message-id="${id}"]`)
    if (node === null || node === undefined) return
    node.scrollIntoView({ block: 'center' })
    setFlash(id)
    window.setTimeout(() => setFlash((current) => (current === id ? null : current)), 1200)
  }

  const numbers = useMemo(
    () => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])),
    [tasks],
  )
  const detail = tasks.find((task) => task.id === selectedTask) ?? null

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
            onClose={() => selectTask(detail.id)}
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
                <MessageRow
                  key={message.id}
                  message={message}
                  roles={roles}
                  numbers={numbers}
                  flash={flash}
                  onQuote={(target) =>
                    setQuote(project, {
                      id: target.id,
                      author: target.author,
                      text: target.text,
                    })
                  }
                  onJump={jumpTo}
                />
              ))
            )}
          </div>
        )}
        {/* Room actions answer here too; the banner sits above the composer,
            in the room's own content band, instead of below both. */}
        <NoticeBanner />
        <RoomComposer roles={roles} />
      </div>
    </div>
  )
}

function MessageRow({
  message,
  roles,
  numbers,
  flash,
  onQuote,
  onJump,
}: {
  message: SwarmMessage
  roles: SwarmRole[]
  numbers: TaskNumbers
  flash: string | null
  onQuote(message: SwarmMessage): void
  onJump(id: string): void
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
  const ref = message.ref ?? null
  return (
    <div
      className={css.message}
      data-swarm-message
      data-message-id={message.id}
      data-author-kind={kind}
      data-own={kind === 'user' || undefined}
      data-flash={flash === message.id || undefined}
    >
      <div className={css.messageHead}>
        {kind !== 'system' && <span className={css.dot} data-kind={kind} aria-hidden />}
        <span className={css.author}>{name}</span>
        {number !== undefined && (
          <span className={css.pill} data-swarm-pill>
            {t('swarm.taskPill', { n: number })}
          </span>
        )}
        {kind !== 'system' && (
          <button
            type="button"
            className={css.quoteAction}
            data-swarm-quote
            aria-label={t('swarm.quoteAction')}
            onClick={() => onQuote(message)}
          >
            {t('swarm.quoteAction')}
          </button>
        )}
      </div>
      {ref !== null && (
        <button
          type="button"
          className={css.quoteCard}
          data-swarm-quote-card
          onClick={() => onJump(ref.id)}
        >
          <span className={css.quoteCardAuthor}>
            {roles.find((role) => role.id === ref.author.id)?.name ?? ref.author.id}
          </span>
          <span className={css.quoteCardText}>{ref.text}</span>
        </button>
      )}
      <div className={css.text}>{message.text}</div>
      {(message.images ?? []).length > 0 && (
        <div className={css.chips}>
          {(message.images ?? []).map((path) => (
            <span key={path} className={css.chip} data-swarm-image>
              {path.split('/').pop() ?? path}
            </span>
          ))}
        </div>
      )}
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
        <span className={panels.rowCaption}>@{task.assignee}</span>
        <span className={panels.taskState} data-state={task.state}>
          {t(`swarm.task.${task.state}`)}
        </span>
        <button
          type="button"
          className={`${panels.ghost} ${css.taskPanelClose}`}
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
          <p className={panels.blockEmpty}>
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