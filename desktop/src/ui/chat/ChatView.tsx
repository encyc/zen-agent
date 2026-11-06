/** Chat transcript view: scroll ownership (follow-tail), node rendering and
 * the empty-state hero — the dsh ChatView shape. */

import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { retryableNodeId, useChatStore, type ChatNode, type ToolNode, type UserNode } from '../../state/chatStore'
import { groupTurns, type ChatTurn } from '../../state/turns'
import { loadRecentProjects, useAppStore } from '../../state/appStore'
import { useSessionStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { HomeHero, pickProjectFolder } from '../HomeHero'
import { UserMessage, SkillMessage } from './MessageItem'
import { AssistantNodeView } from './AssistantNodeView'
import { TurnProcess } from './TurnProcess'
import { NoticeRow, CommandCard, UsageRow } from './AuxRows'
import { ArrowDownIcon } from '../icons'
import css from './ChatView.module.css'

export function ChatView() {
  const nodes = useChatStore((state) => state.nodes)
  const revision = useChatStore((state) => state.revision)
  const busy = useAppStore((state) => state.busy)
  const project = useSessionStore(
    (state) => state.sessions.find((session) => session.path === state.currentPath)?.project ?? null,
  )
  const t = useT()
  const [recents] = useState(loadRecentProjects)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const tailRef = useRef<HTMLDivElement | null>(null)
  const [follow, setFollow] = useState(true)
  const [showJump, setShowJump] = useState(false)

  // Follow the tail while the user is at (or near) the bottom; any upward
  // scroll detaches until they return. Same behavior contract as dsh.
  useEffect(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const onScroll = () => {
      const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight
      setFollow(distance < 48)
      setShowJump(distance > 160)
    }
    scroller.addEventListener('scroll', onScroll, { passive: true })
    return () => scroller.removeEventListener('scroll', onScroll)
  }, [])

  // New content keeps the view pinned when following; else reveal the jump pill.
  useEffect(() => {
    if (!follow) return
    tailRef.current?.scrollIntoView({ block: 'end' })
  }, [revision, follow])

  const switchProject = async (path: string) => {
    if (!window.confirm(t('home.switchConfirm', { path }))) return
    await useAppStore.getState().disconnect()
    await useAppStore.getState().connect(undefined, path)
  }
  const pickAndSwitchProject = async () => {
    const path = await pickProjectFolder()
    if (path !== null) await switchProject(path)
  }

  const isEmpty = nodes.length === 0
  // Regenerate re-runs the last turn; `/retry` already owns the transcript
  // reset and session refresh, so the action reuses that command.
  const retryId = retryableNodeId(nodes)
  const canRetry = !busy && retryId !== null
  const onRetry = () => void useAppStore.getState().runCommand('retry', '')
  const turns = groupTurns(nodes, busy)
  const liveTurn = turns[turns.length - 1]
  const showRunningRow = busy && (liveTurn === undefined || liveTurn.tools.length === 0)

  return (
    <div className={css.root}>
      <div className={css.scroll} ref={scrollerRef}>
        {isEmpty ? (
          <div className={css.emptyHost}>
            <HomeHero
              connected
              projectName={project}
              recents={recents}
              onPickFolder={() => void pickAndSwitchProject()}
              onUseCurrent={() => undefined}
              onOpenRecent={(path) => void switchProject(path)}
            />
          </div>
        ) : (
          <div className={css.flow}>
            {turns.map((turn) => (
              <TurnSeat
                key={turn.id}
                turn={turn}
                retryable={canRetry}
                retryId={retryId}
                onRetry={onRetry}
              />
            ))}
            {showRunningRow && <RunningRow />}
          </div>
        )}
        <div ref={tailRef} className={css.tailPad} />
      </div>
      {showJump && (
        <button
          type="button"
          className={css.jump}
          onClick={() => {
            tailRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
            setFollow(true)
          }}
          aria-label="scroll to bottom"
        >
          <ArrowDownIcon size={13} />
        </button>
      )}
    </div>
  )
}

function TurnSeat({
  turn,
  retryable,
  retryId,
  onRetry,
}: {
  turn: ChatTurn
  retryable: boolean
  retryId: string | null
  onRetry(): void
}) {
  const rows: ReactNode[] = []
  let run: ToolNode[] = []
  const flush = () => {
    if (run.length === 0) return
    rows.push(
      <TurnProcess
        key={`process-${run[0].id}`}
        tools={run}
        durationMs={turn.durationMs}
        running={turn.running}
      />,
    )
    run = []
  }
  if (turn.user !== null) {
    rows.push(
      <UserMessage key={turn.user.id} text={turn.user.text} steering={turn.user.steering} />,
    )
  }
  for (const node of turn.nodes) {
    if (node.kind === 'tool') {
      run.push(node)
      continue
    }
    flush()
    rows.push(
      <NodeSeat
        key={node.id}
        node={node}
        retryable={retryable && node.id === retryId}
        onRetry={onRetry}
      />,
    )
  }
  flush()
  return <Fragment>{rows}</Fragment>
}

function NodeSeat({
  node,
  retryable,
  onRetry,
}: {
  node: Exclude<ChatNode, ToolNode | UserNode>
  retryable: boolean
  onRetry(): void
}) {
  switch (node.kind) {
    case 'skill':
      return <SkillMessage name={node.name} args={node.args} body={node.body} />
    case 'assistant':
      return <AssistantNodeView node={node} canRetry={retryable} onRetry={onRetry} />
    case 'notice':
      return (
        <NoticeRow
          variant={node.variant}
          text={node.text}
          tokensBefore={node.tokensBefore}
        />
      )
    case 'command':
      return <CommandCard name={node.name} argument={node.argument} message={node.message} />
    case 'usage':
      return <UsageRow usage={node.usage} turnMs={node.turnMs} termination={node.termination} />
    default:
      return null
  }
}

function RunningRow() {
  const t = useT()
  return (
    <div className={css.runningRow}>
      <span className={css.runningDot} aria-hidden />
      <span className={css.runningLabel}>{t('status.running')}</span>
    </div>
  )
}
