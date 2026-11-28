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
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import {
  retryableNodeId,
  useChatStore,
  type ChatNode,
  type ChatStoreApi,
  type ToolNode,
  type UserNode,
} from '../../state/chatStore'
import { groupTurns, type ChatTurn } from '../../state/turns'
import {
  TRANSCRIPT_WINDOW_MIN,
  earlierRow,
  growTranscriptWindow,
  reduceTranscriptWindow,
  shrinkTranscriptWindow,
  tailWindow,
} from '../../state/transcriptWindow'
import { useTranscriptScroll } from './useTranscriptScroll'
import { useAppStore } from '../../state/appStore'
import { useAskStore, useProjectStore, useSessionStore, useSettingsStore } from '../../state/stores'
import { pendingForProject } from '../../state/pendingProject'
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
import { AskCard } from '../ask/AskCard'
import { RetryRow, FailureRow } from './RetryRow'
import { ArrowDownIcon } from '../icons'
import { RunningMark } from '../RunningMark'
import css from './ChatView.module.css'

/** One window-growth step per gesture; see `growWindow`. */
const GROWTH_COOLDOWN_MS = 500

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

/** One conversation transcript. Without props this is the shell's focused
 * session exactly as before; a session pane passes its own chat store, its
 * own busy flag and its own older-page/retry routes, so the same scrolling,
 * windowing and node rendering serve both surfaces. */
export interface ChatViewProps {
  chat?: ChatStoreApi
  busy?: boolean
  onLoadOlder?: () => Promise<boolean>
  onRetry?: () => void
  /** Replaces the HomeHero empty state (panes never offer project picking). */
  emptyState?: ReactNode
  /** Whether this transcript instance hosts the ask card. Only one may: the
   * queue is global and an untagged request would otherwise render in every
   * pane. Panes pass it only for the focused one. */
  askCard?: boolean
}

export function ChatView(props: ChatViewProps) {
  const chat = props.chat ?? useChatStore
  const nodes = useSyncExternalStore(chat.subscribe, () => chat.getState().nodes)
  const revision = useSyncExternalStore(chat.subscribe, () => chat.getState().revision)
  const hasMoreBefore = useSyncExternalStore(chat.subscribe, () => chat.getState().hasMoreBefore)
  const appBusy = useAppStore((state) => state.busy)
  const busy = props.busy ?? appBusy
  const project = useSessionStore(
    (state) => state.sessions.find((session) => session.path === state.currentPath)?.project ?? null,
  )
  // The transcript identity the scroll hook resets against. A pane passes its
  // own chat store and is keyed by path upstream, so only the focused view needs
  // this; for a pane the mount itself is the reset.
  const currentPath = useSessionStore((state) => state.currentPath)
  const t = useT()
  const recents = useProjectStore((state) => state.projects)
  const chatWidth = useSettingsStore((state) => state.chatWidth)
  const setChatWidth = useSettingsStore((state) => state.setChatWidth)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const tailRef = useRef<HTMLDivElement | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const askSeatRef = useRef<HTMLDivElement | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  // Same slot the card itself resolves — AskCard reads the app store's project,
  // so the pill must not disagree with it about whether a question is pending.
  const askProject = useAppStore((state) => state.project)
  const askQueue = useAskStore((state) => state.queue)
  const askPending =
    props.askCard !== false &&
    pendingForProject(askQueue, askProject, currentPath).length > 0

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
    const top =
      scroller.scrollTop +
      row.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top -
      12
    markProgrammatic(top)
    scroller.scrollTo({ top, behavior: 'smooth' })
  }

  // One step per window budget. Scrolling the earlier row into view fires the
  // scroll-at-top handler, then the click fires it again (a smooth scroll can
  // also land after the commit). A short cooldown keeps one gesture to one
  // growth; otherwise the long-transcript e2e saw 140 rows where 100 were
  // promised.
  const growthAt = useRef(0)
  const growWindow = useCallback(() => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    const grown = growTranscriptWindow(windowSize, nodes.length)
    if (grown === windowSize) return
    if (Date.now() - growthAt.current < GROWTH_COOLDOWN_MS) return
    growthAt.current = Date.now()
    // Mounting rows above the viewport moves everything the reader is looking
    // at; hold the distance from the bottom so the content stays put.
    anchorRef.current = scroller.scrollHeight - scroller.scrollTop
    setWindowSize(grown)
  }, [windowSize, nodes.length])

  // The loaded rows are all mounted and the session has more history on the
  // server: fetch the next older page and mount it in one commit, anchored
  // like a window growth so the reader's place does not jump.
  const fetchOlder = props.onLoadOlder ?? (() => useAppStore.getState().loadOlderTranscript())
  // The top handler and the earlier-row click can both ask for a page before
  // the first request resolves; one page per cursor, not two.
  const loadingOlder = useRef(false)
  const loadOlder = useCallback(async () => {
    if (loadingOlder.current) return
    loadingOlder.current = true
    try {
      const scroller = scrollerRef.current
      const before = chat.getState().nodes.length
      if (!(await fetchOlder())) return
      const added = chat.getState().nodes.length - before
      if (added <= 0) return
      if (scroller !== null) {
        anchorRef.current = scroller.scrollHeight - scroller.scrollTop
      }
      setWindowSize((size) => size + added)
    } finally {
      loadingOlder.current = false
    }
  }, [chat, fetchOlder])

  const revealTop = useCallback(() => {
    if (transcriptWindow.bounded) {
      growWindow()
    } else if (hasMoreBefore) {
      void loadOlder()
    }
  }, [transcriptWindow.bounded, hasMoreBefore, growWindow, loadOlder])

  // Scroll ownership (mode, jump pill, unseen count) lives in the hook; its
  // callbacks are the two window moves this view owns.
  const { snap, snapRef, markProgrammatic, publish, attach } = useTranscriptScroll({
    scrollerRef,
    nodes,
    transcriptKey: props.chat === undefined ? currentPath : null,
    onAtTop: revealTop,
    onFollowing: () => {
      const shrunk = shrinkTranscriptWindow(windowSize)
      if (shrunk !== windowSize) setWindowSize(shrunk)
    },
    onActiveChange: setActiveId,
  })

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
    if (snapRef.current.mode === 'following') {
      markProgrammatic()
      scroller.scrollTop = scroller.scrollHeight
    }
  }, [transcriptWindow.mounted])

  // New content keeps the view pinned while following. The unseen count that
  // the pill shows is the hook's business.
  useEffect(() => {
    if (snapRef.current.mode === 'following') tailRef.current?.scrollIntoView({ block: 'end' })
  }, [revision, snapRef])

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
  const onRetry = props.onRetry ?? (() => void useAppStore.getState().runCommand('retry', ''))
  const turns = groupTurns(visible, busy)
  const liveTurn = turns[turns.length - 1]
  const showRunningRow = busy && (liveTurn === undefined || liveTurn.tools.length === 0)

  const askSeat =
    props.askCard === false ? null : (
      <div ref={askSeatRef} className={css.askSeat} data-ask-seat>
        <AskCard />
      </div>
    )

  // The card is a transcript row, so its height is the transcript's bottom
  // band: the floating pill/minimap/handles must sit above it, and a card that
  // grows while the reader follows the tail must stay in view. One measurement
  // serves both (the fade already clears it — see the design doc).
  useLayoutEffect(() => {
    const node = askSeatRef.current
    const root = rootRef.current
    if (root === null) return
    // No seat means this instance no longer hosts the card (another pane took
    // it, or this pane was just told not to render one). Give the band back:
    // leaving the last height behind parks the pill and minimap above empty
    // space for the rest of the session.
    if (node === null) {
      root.style.setProperty('--ask-card-height', '0px')
      return
    }
    const publish = () => {
      const height = Math.round(node.getBoundingClientRect().height)
      root.style.setProperty('--ask-card-height', `${Math.max(0, height)}px`)
      const scroller = scrollerRef.current
      if (scroller === null || height === 0) return
      if (snapRef.current.mode !== 'following') return
      markProgrammatic()
      scroller.scrollTop = scroller.scrollHeight
    }
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(node)
    return () => observer.disconnect()
    // The seat is a different DOM node in the empty and the non-empty branch:
    // a branch flip swaps the ref without re-running the effect otherwise.
  }, [markProgrammatic, isEmpty, props.askCard])

  const revealEarlier = () => {
    const scroller = scrollerRef.current
    if (scroller === null) return
    if (scroller.scrollTop <= 8) {
      revealTop()
      return
    }
    markProgrammatic(0)
    publish({ ...snapRef.current, mode: 'free', showJump: true })
    scroller.scrollTo({ top: 0, behavior: 'smooth' })
  }
  // The row also covers server-side history the snapshot never loaded: once
  // every loaded row is mounted a click fetches the next page directly.
  const earlier = earlierRow(transcriptWindow.bounded, transcriptWindow.hiddenAbove, hasMoreBefore)

  return (
    <div className={css.root} ref={rootRef} data-chat-pane>
      <div className={css.scroll} ref={attach} data-chat-scroll>
        {isEmpty ? (
          <>
            <div className={css.emptyHost}>
              {props.emptyState ?? (
                <HomeHero
                  projectName={project}
                  recents={recents}
                  onPickFolder={() => void pickAndSwitchProject()}
                  onOpenRecent={(path) => void switchProject(path)}
                />
              )}
            </div>
            {askSeat}
          </>
        ) : (
          <div className={css.flow} data-chat-flow>
            {earlier.visible && (
              <div className={css.earlierRow}>
                <button
                  type="button"
                  className={css.earlier}
                  data-earlier
                  onClick={earlier.loadsPage ? () => void loadOlder() : revealEarlier}
                >
                  {t('chat.showEarlier', { count: earlier.count })}
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
            {askSeat}
          </div>
        )}
        <div ref={tailRef} />
      </div>
      {!isEmpty && (
        <ConversationMinimap users={userRows} activeId={activeId} onJump={jumpTo} />
      )}
      {props.chat === undefined && (
        <ChatWidthHandles width={clampChatWidth(chatWidth)} onChange={setChatWidth} />
      )}
      {(snap.showJump || askPending) && (
        <button
          type="button"
          className={css.jump}
          data-jump
          data-ask={askPending || undefined}
          data-unseen={snap.unseen > 0 || undefined}
          onClick={() => {
            const scroller = scrollerRef.current
            markProgrammatic(scroller === null ? 0 : scroller.scrollHeight - scroller.clientHeight)
            publish({ mode: 'following', showJump: false, atTop: false, unseen: 0 })
            tailRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
          }}
          aria-label={
            snap.unseen > 0
              ? t('chat.jumpUnseen', { count: snap.unseen })
              : askPending
                ? t('chat.backToQuestion')
                : 'scroll to bottom'
          }
        >
          <ArrowDownIcon size={13} />
          {snap.unseen > 0 && (
            <span className={css.jumpBadge}>{snap.unseen > 9 ? '9+' : snap.unseen}</span>
          )}
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
        goal={turn.user.goal}
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
    case 'retry':
      return <RetryRow node={node} />
    case 'failure':
      return <FailureRow node={node} />
    case 'command':
      return <CommandCard name={node.name} argument={node.argument} message={node.message} />
    case 'usage':
      return (
        <UsageRow
          usage={node.usage}
          turnMs={node.turnMs}
          termination={node.termination}
          detail={node.detail}
        />
      )
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
