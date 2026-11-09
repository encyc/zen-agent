/** One task child's real conversation in the work panel: the same rows the
 * main transcript uses, polled while the child (or its parent run) is live. */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import {
  TRANSCRIPT_WINDOW_MIN,
  growTranscriptWindow,
  reduceTranscriptWindow,
  tailWindow,
} from '../../state/transcriptWindow'
import { useStatusStore, type SubagentPanelTarget } from '../../state/stores'
import { nodesFromViews, type ChatNode } from '../../state/chatStore'
import { mergeSubagentPage, nextSubagentCursor } from '../../state/subagentPage'
import type { MessageView, SubagentTranscriptResult, ToolCallView } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { formatDuration } from '../format'
import { BotIcon } from '../icons'
import { StateDot } from '../primitives'
import { AssistantNodeView } from '../chat/AssistantNodeView'
import { UserMessage, SkillMessage } from '../chat/MessageItem'
import { ToolRowView } from '../chat/ToolRowView'
import css from './SubagentTranscript.module.css'

const POLL_MS = 1000
const REQUEST_TIMEOUT_MS = 8000
const STICK_PX = 80

type View = MessageView | ToolCallView

export function SubagentTranscript({ target }: { target: SubagentPanelTarget }) {
  const t = useT()
  const [views, setViews] = useState<View[] | null>(null)
  /** The accumulated rows; the poll cursor and the merge read it without a
   * re-render dependency. */
  const viewsRef = useRef<View[] | null>(null)
  const [available, setAvailable] = useState(true)
  const hasViews = useRef(false)
  const requestSeq = useRef(0)
  const stick = useRef(true)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  // Mounted history is a trailing window (D261 parity with the main
  // transcript): a bounded first commit, then the steady-state budget, grown
  // upward when the reader reaches the top.
  const [windowSize, setWindowSize] = useState(TRANSCRIPT_WINDOW_MIN)
  const [initialCommit, setInitialCommit] = useState(true)
  const anchorRef = useRef<number | null>(null)
  const mountedRef = useRef(0)

  const view = useStatusStore((state) =>
    state.status?.subagents?.find((entry) => entry.call_id === target.callId),
  )
  const busy = useStatusStore((state) => state.status?.running === true)
  // Poll while either side is live: the child's rows arrive with its events,
  // and the parent's status cadence is what settles the final snapshot.
  const active = busy || view?.status === 'running'

  const load = useCallback(async () => {
    const seq = (requestSeq.current += 1)
    // Ask only for what the panel does not hold; the cursor stays on the
    // earliest running card (plus one tail row) so in-place growth and a
    // settlement both land.
    const after = nextSubagentCursor(viewsRef.current)
    const payload: Record<string, unknown> = {
      type: 'subagent_transcript',
      call_id: target.callId,
    }
    if (after !== undefined) payload.after = after
    try {
      const result = await useAppStore
        .getState()
        .client()
        .send<SubagentTranscriptResult>(payload, REQUEST_TIMEOUT_MS)
      if (seq !== requestSeq.current) return
      if (result.ok !== true) {
        if (!hasViews.current) setAvailable(false)
        return
      }
      hasViews.current = true
      setAvailable(result.available !== false)
      const merged = mergeSubagentPage(viewsRef.current, result)
      viewsRef.current = merged
      // The merge returns the previous array identity when no row changed, so
      // React skips the rebuild; a settlement or an append is a new array.
      setViews(merged)
    } catch {
      // A failed poll must not wipe rows already on screen.
      if (seq === requestSeq.current && !hasViews.current) setAvailable(false)
    }
  }, [target.callId])

  useEffect(() => {
    hasViews.current = false
    viewsRef.current = null
    stick.current = true
    setViews(null)
    setAvailable(true)
    setWindowSize(TRANSCRIPT_WINDOW_MIN)
    setInitialCommit(true)
    anchorRef.current = null
    mountedRef.current = 0
    void load()
  }, [load])

  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [active, load])

  const nodes = useMemo(() => nodesFromViews(views ?? []), [views])

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
  // Only the trailing slice mounts; older loaded rows stay in the snapshot.
  const visible = useMemo(
    () => tailWindow(nodes, transcriptWindow.mounted),
    [nodes, transcriptWindow.mounted],
  )

  const growWindow = useCallback(() => {
    const element = scrollRef.current
    if (element === null) return
    const grown = growTranscriptWindow(windowSize, nodes.length)
    if (grown === windowSize) return
    // Mounting rows above the viewport moves everything in view: hold the
    // distance from the bottom so the reader stays where they were.
    anchorRef.current = element.scrollHeight - element.scrollTop
    setWindowSize(grown)
  }, [windowSize, nodes.length])

  // The anchor branch runs first so a window growth restores the pre-growth
  // offset; otherwise a sticking reader follows the tail. `visible` is in the
  // dependencies because `mounted` only tracks the row count: the live row
  // growing its text (and a full window shifting its tail) leaves `mounted`
  // unchanged, and depending on it alone would strand the newest lines below
  // the fold. The poll skip above keeps this from firing on unchanged polls.
  useLayoutEffect(() => {
    const element = scrollRef.current
    if (element === null) return
    const mounted = transcriptWindow.mounted
    const previous = mountedRef.current
    mountedRef.current = mounted
    if (anchorRef.current !== null && mounted > previous) {
      element.scrollTop = element.scrollHeight - anchorRef.current
      anchorRef.current = null
      return
    }
    anchorRef.current = null
    if (!stick.current) return
    element.scrollTop = element.scrollHeight
  }, [transcriptWindow.mounted, visible])

  const onScroll = () => {
    const element = scrollRef.current
    if (element === null) return
    stick.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < STICK_PX
    // Reaching the top mounts the next slice of loaded history.
    if (transcriptWindow.bounded && element.scrollTop <= 8) growWindow()
  }

  const name = view?.name || target.name
  const status = view?.status
  const meta = [
    view !== undefined && view.duration_ms > 0 ? formatDuration(view.duration_ms) : '',
    view !== undefined && view.tokens > 0 ? `${formatCount(view.tokens)} tok` : '',
    view !== undefined && view.cost > 0 ? `$${view.cost.toFixed(4)}` : '',
  ].filter((part) => part !== '')

  return (
    <div className={css.root} data-testid="subagent-transcript" data-call-id={target.callId}>
      <div className={css.status}>
        <div className={css.headline}>
          <span className={css.agentIcon} aria-hidden>
            <BotIcon size={13} />
          </span>
          <span className={css.name} title={name}>
            {name}
          </span>
          {status !== undefined && (
            <span className={css.state} data-state={status}>
              {status === 'running' && <StateDot state="running" size={6} />}
              {t(`panel.subagentStatus.${status}`)}
            </span>
          )}
        </div>
        {(meta.length > 0 || view?.transcript_truncated === true) && (
          <div className={css.meta}>
            {meta.map((item, index) => (
              <span key={index}>{item}</span>
            ))}
            {view?.transcript_truncated === true && (
              <span>{t('panel.subagentTruncated')}</span>
            )}
          </div>
        )}
      </div>
      <div
        ref={scrollRef}
        className={css.scroll}
        role="log"
        aria-live="polite"
        aria-label={`${t('panel.subagent')}: ${name}`}
        tabIndex={0}
        onScroll={onScroll}
      >
        <div className={css.rows}>
          {available === false ? (
            <p className={css.empty}>{t('panel.subagentUnavailable')}</p>
          ) : views === null ? (
            <p className={css.empty}>{t('panel.subagentLoading')}</p>
          ) : nodes.length === 0 ? (
            <p className={css.empty}>{t('panel.subagentEmpty')}</p>
          ) : (
            visible.map((node) => <TranscriptRow key={node.id} node={node} />)
          )}
        </div>
      </div>
      <footer className={css.footer}>{t('panel.subagentReadonly')}</footer>
    </div>
  )
}

function TranscriptRow({ node }: { node: ChatNode }) {
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

function formatCount(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}
