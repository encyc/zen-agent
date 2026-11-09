/** Chat transcript view: scroll ownership (follow-tail), node rendering and
 * the empty-state hero — the dsh ChatView shape. */

import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { retryableNodeId, useChatStore, type ChatNode, type ToolNode, type UserNode } from '../../state/chatStore'
import { groupTurns, type ChatTurn } from '../../state/turns'
import {
  TRANSCRIPT_WINDOW_MIN,
  growTranscriptWindow,
  reduceTranscriptWindow,
  shrinkTranscriptWindow,
  tailWindow,
} from '../../state/transcriptWindow'
import { useAppStore } from '../../state/appStore'
import { useProjectStore, useSessionStore, useSettingsStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { HomeHero } from '../HomeHero'
import { pickProjectFolder } from '../sessions/fileDialog'
import { clampChatWidth } from '../columns'
import { UserMessage, SkillMessage } from './MessageItem'
import { AssistantNodeView } from './AssistantNodeView'
import { TurnProcess } from './TurnProcess'
import { ConversationMinimap } from './ConversationMinimap'
import { ChatWidthHandles } from './ChatWidthHandles'
import { NoticeRow, CommandCard, UsageRow } from './AuxRows'
import { ArrowDownIcon } from '../icons'
import { RunningMark } from '../RunningMark'
import css from './ChatView.module.css'

/** The rail only cares about user rows; a stream of assistant deltas rebuilds
 * `nodes` without touching them, so compare identity instead of rebuilding. */
function sameUserRows(rows: UserNode[], nodes: ChatNode[]): boolean {
  let index = 0
  for (const node of nodes) {
    if (node.kind !== 'user') continue
    if (rows[index] !== node) return false
    index += 1
  }
  return index === rows.length
}

export function ChatView() {
  const nodes = useChatStore((state) => state.nodes)
  const revision = useChatStore((state) => state.revision)
  const hasMoreBefore = useChatStore((state) => state.hasMoreBefore)
  const busy = useAppStore((state) => state.busy)
  const project = useSessionStore(
    (state) => state.sessions.find((session) => session.path === state.currentPath)?.project ?? null,
  )
  const t = useT()
  const recents = useProjectStore((state) => state.projects)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  const setChatWidth = useSettingsStore((state) => state.setChatWidth)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const tailRef = useRef<HTMLDivElement | null>(null)
  const [follow, setFollow] = useState(true)
  const [showJump, setShowJump] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)

  // Mounted history is a trailing window over the loaded nodes (D261 parity):
  // a fast first commit, then the steady-state budget, grown upward on demand.
  const [windowSize, setWindowSize] = useState(TRANSCRIPT_WINDOW_MIN)
  const [initialCommit, setInitialCommit] = useState(true)
  const anchorRef = useRef<number | null>(null)

  useEffect(() => {
    if (nodes.length !== 0) return
    setWindowSize(TRANSCRIPT_WINDOW_MIN)
    setInitialCommit(true)
  }, [nodes.length])

  useEffect(() => {
    if (!initialCommit || nodes.length === 0) return
    const frame = requestAnimationFrame(() => setInitialCommit(false))
    return () => cancelAnimationFrame(frame)
  }, [initialCommit, nodes.length])

  const transcriptWindow = reduceTranscriptWindow({
    historyLength: nodes.length,
    windowSize,
    initialCommit,
  })
  const visible = useMemo(
    () => tailWindow(nodes, transcriptWindow.mounted),
    [nodes, transcriptWindow.mounted],
  )

  // The rail spans the whole loaded session, not just the mounted window.
  const userRowsRef = useRef<UserNode[]>([])
  if (!sameUserRows(userRowsRef.current, nodes)) {
    userRowsRef.current = nodes.filter((node): node is UserNode => node.kind === 'user')
  }
  const userRows = userRowsRef.current

  const findRow = (id: string) => {
    const scroller = scrollerRef.current
    if (scroller === null) return undefined
    return Array.from(scroller.querySelectorAll<HTMLElement>('[data-minimap-id]')).find(
      (row) => row.dataset.minimapId === id,
    )
  }
  const scrollToRow = (row: HTMLElement) => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    setFollow(false)
    scroller.scrollTo({
      top:
        scroller.scrollTop +
        row.getBoundingClientRect().top -
        scroller.getBoundingClientRect().top -
        12,
      behavior: 'smooth',
    })
  }

  const growWindow = useCallback(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const grown = growTranscriptWindow(windowSize, nodes.length)
    if (grown === windowSize) return
    // Mounting rows above the viewport moves everything the reader is looking
    // at; hold the distance from the bottom so the content stays put.
    anchorRef.current = scroller.scrollHeight - scroller.scrollTop
    setWindowSize(grown)
  }, [windowSize, nodes.length])

  // The loaded rows are all mounted and the session has more history on the
  // server: fetch the next older page and mount it in one commit, anchored
  // like a window growth so the reader's place does not jump.
  const loadOlder = useCallback(async () => {
    const scroller = scrollerRef.current
    const before = useChatStore.getState().nodes.length
    if (!(await useAppStore.getState().loadOlderTranscript())) return
    const added = useChatStore.getState().nodes.length - before
    if (added <= 0) return
    if (scroller !== null) {
      anchorRef.current = scroller.scrollHeight - scroller.scrollTop
    }
    setWindowSize((size) => size + added)
  }, [])

  const revealTop = useCallback(() => {
    if (transcriptWindow.bounded) {
      growWindow()
    } else if (hasMoreBefore) {
      void loadOlder()
    }
  }, [transcriptWindow.bounded, hasMoreBefore, growWindow, loadOlder])

  const pendingJumpRef = useRef<string | null>(null)
  const mountedRef = useRef(0)
  useLayoutEffect(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const mounted = transcriptWindow.mounted
    const previous = mountedRef.current
    mountedRef.current = mounted
    if (mounted <= previous) {
      // A growth that never mounted (or a shorter transcript) has no anchor to
      // consume; keeping it would displace the next real growth.
      anchorRef.current = null
      return
    }
    const pending = pendingJumpRef.current
    if (pending !== null) {
      pendingJumpRef.current = null
      anchorRef.current = null
      const row = findRow(pending)
      if (row !== undefined) scrollToRow(row)
      return
    }
    if (anchorRef.current !== null) {
      scroller.scrollTop = scroller.scrollHeight - anchorRef.current
      anchorRef.current = null
      return
    }
    if (follow) scroller.scrollTop = scroller.scrollHeight
  }, [transcriptWindow.mounted, follow])

  // Follow the tail while the user is at (or near) the bottom; any upward
  // scroll detaches until they return. Reaching the top of the mounted window
  // mounts the next slice of loaded history instead of letting it strand; at
  // the top of everything loaded it fetches an older page. Returning to the
  // tail releases the rows mounted while browsing (D261 windowing).
  useEffect(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const onScroll = () => {
      const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight
      const following = distance < 48
      setFollow(following)
      setShowJump(distance > 160)
      const anchor = scroller.getBoundingClientRect().top + scroller.clientHeight * 0.3
      let next: string | null = null
      scroller.querySelectorAll<HTMLElement>('[data-minimap-kind="user"]').forEach((row) => {
        if (row.getBoundingClientRect().top <= anchor) next = row.dataset.minimapId ?? null
      })
      setActiveId(next)
      if (scroller.scrollTop <= 8) revealTop()
      if (following) {
        const shrunk = shrinkTranscriptWindow(windowSize)
        if (shrunk !== windowSize) setWindowSize(shrunk)
      }
    }
    scroller.addEventListener('scroll', onScroll, { passive: true })
    return () => scroller.removeEventListener('scroll', onScroll)
  }, [revealTop, windowSize])

  // New content keeps the view pinned when following; else reveal the jump pill.
  useEffect(() => {
    if (!follow) return
    tailRef.current?.scrollIntoView({ block: 'end' })
  }, [revision, follow])

  const switchProject = async (path: string) => {
    if (!window.confirm(t('home.switchConfirm', { path }))) return
    await useAppStore.getState().openProject(path)
  }
  const pickAndSwitchProject = async () => {
    const path = await pickProjectFolder()
    if (path !== null) await switchProject(path)
  }
  const jumpTo = (id: string) => {
    const mounted = findRow(id)
    if (mounted !== undefined) {
      scrollToRow(mounted)
      return
    }
    const index = nodes.findIndex((node) => node.id === id)
    if (index < 0) return
    // The window is a trailing slice, so an older row cannot mount alone: grow
    // over everything between it and the tail, then land on it after the commit.
    pendingJumpRef.current = id
    anchorRef.current = null
    setWindowSize(Math.max(windowSize, nodes.length - index))
  }

  const isEmpty = nodes.length === 0
  // Regenerate re-runs the last turn; `/retry` already owns the transcript
  // reset and session refresh, so the action reuses that command.
  const retryId = retryableNodeId(visible)
  const canRetry = !busy && retryId !== null
  const onRetry = () => void useAppStore.getState().runCommand('retry', '')
  const turns = groupTurns(visible, busy)
  const liveTurn = turns[turns.length - 1]
  const showRunningRow = busy && (liveTurn === undefined || liveTurn.tools.length === 0)

  const revealEarlier = () => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    if (scroller.scrollTop <= 8) {
      revealTop()
      return
    }
    setFollow(false)
    scroller.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className={css.root}>
      <div className={css.scroll} ref={scrollerRef} data-chat-scroll>
        {isEmpty ? (
          <div className={css.emptyHost}>
            <HomeHero
              projectName={project}
              recents={recents}
              onPickFolder={() => void pickAndSwitchProject()}
              onOpenRecent={(path) => void switchProject(path)}
            />
          </div>
        ) : (
          <div className={css.flow} data-chat-flow>
            {transcriptWindow.bounded && (
              <div className={css.earlierRow}>
                <button
                  type="button"
                  className={css.earlier}
                  data-earlier
                  onClick={revealEarlier}
                >
                  {t('chat.showEarlier', { count: transcriptWindow.hiddenAbove })}
                </button>
              </div>
            )}
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
        <div ref={tailRef} />
      </div>
      {!isEmpty && (
        <ConversationMinimap users={userRows} activeId={activeId} onJump={jumpTo} />
      )}
      <ChatWidthHandles width={clampChatWidth(chatWidth)} onChange={setChatWidth} />
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
      <UserMessage
        key={turn.user.id}
        text={turn.user.text}
        steering={turn.user.steering}
        anchor={turn.user.id}
      />,
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
        anchor={node.kind === 'assistant' ? node.id : undefined}
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
  anchor,
  retryable,
  onRetry,
}: {
  node: Exclude<ChatNode, ToolNode | UserNode>
  anchor?: string
  retryable: boolean
  onRetry(): void
}) {
  switch (node.kind) {
    case 'skill':
      return <SkillMessage name={node.name} args={node.args} body={node.body} />
    case 'assistant':
      return (
        <AssistantNodeView
          node={node}
          anchor={anchor}
          canRetry={retryable}
          onRetry={onRetry}
        />
      )
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
      <RunningMark size={12} />
      <span className={css.runningLabel}>{t('status.running')}</span>
    </div>
  )
}
