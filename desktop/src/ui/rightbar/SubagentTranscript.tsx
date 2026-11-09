/** One task child's real conversation in the work panel: the same rows the
 * main transcript uses, polled while the child (or its parent run) is live. */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useStatusStore, type SubagentPanelTarget } from '../../state/stores'
import { nodesFromViews, type ChatNode } from '../../state/chatStore'
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
  const [available, setAvailable] = useState(true)
  const hasViews = useRef(false)
  const requestSeq = useRef(0)
  const stick = useRef(true)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const view = useStatusStore((state) =>
    state.status?.subagents?.find((entry) => entry.call_id === target.callId),
  )
  const busy = useStatusStore((state) => state.status?.running === true)
  // Poll while either side is live: the child's rows arrive with its events,
  // and the parent's status cadence is what settles the final snapshot.
  const active = busy || view?.status === 'running'

  const load = useCallback(async () => {
    const seq = (requestSeq.current += 1)
    try {
      const result = await useAppStore.getState().client().send<SubagentTranscriptResult>(
        { type: 'subagent_transcript', call_id: target.callId },
        REQUEST_TIMEOUT_MS,
      )
      if (seq !== requestSeq.current) return
      if (result.ok !== true) {
        if (!hasViews.current) setAvailable(false)
        return
      }
      hasViews.current = true
      setAvailable(result.available !== false)
      setViews(result.transcript ?? [])
    } catch {
      // A failed poll must not wipe rows already on screen.
      if (seq === requestSeq.current && !hasViews.current) setAvailable(false)
    }
  }, [target.callId])

  useEffect(() => {
    hasViews.current = false
    stick.current = true
    setViews(null)
    setAvailable(true)
    void load()
  }, [load])

  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [active, load])

  const nodes = useMemo(() => nodesFromViews(views ?? []), [views])

  useLayoutEffect(() => {
    const element = scrollRef.current
    if (element === null || !stick.current) return
    element.scrollTop = element.scrollHeight
  }, [nodes])

  const onScroll = () => {
    const element = scrollRef.current
    if (element === null) return
    stick.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < STICK_PX
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
            nodes.map((node) => <TranscriptRow key={node.id} node={node} />)
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
