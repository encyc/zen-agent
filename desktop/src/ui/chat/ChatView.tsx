/** Chat transcript view: scroll ownership (follow-tail), node rendering and
 * the empty-state hero — the dsh ChatView shape. */

import { useEffect, useRef, useState } from 'react'
import { retryableNodeId, useChatStore, type ChatNode } from '../../state/chatStore'
import { useAppStore } from '../../state/appStore'
import { useT } from '../../i18n/locale'
import { UserMessage, SkillMessage } from './MessageItem'
import { AssistantNodeView } from './AssistantNodeView'
import { ToolRowView } from './ToolRowView'
import { NoticeRow, CommandCard, UsageRow } from './AuxRows'
import { ArrowDownIcon } from '../icons'
import css from './ChatView.module.css'

export function ChatView() {
  const nodes = useChatStore((state) => state.nodes)
  const revision = useChatStore((state) => state.revision)
  const busy = useAppStore((state) => state.busy)
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

  const isEmpty = nodes.length === 0
  // Regenerate re-runs the last turn; `/retry` already owns the transcript
  // reset and session refresh, so the action reuses that command.
  const retryId = retryableNodeId(nodes)
  const canRetry = !busy && retryId !== null
  const onRetry = () => void useAppStore.getState().runCommand('retry', '')

  return (
    <div className={css.root}>
      <div className={css.scroll} ref={scrollerRef}>
        {isEmpty ? (
          <EmptyHero />
        ) : (
          <div className={css.flow}>
            {nodes.map((node) => (
              <NodeSeat
                key={node.id}
                node={node}
                retryable={canRetry && node.id === retryId}
                onRetry={onRetry}
              />
            ))}
            {busy && <RunningRow />}
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

function NodeSeat({
  node,
  retryable,
  onRetry,
}: {
  node: ChatNode
  retryable: boolean
  onRetry(): void
}) {
  switch (node.kind) {
    case 'user':
      return <UserMessage text={node.text} steering={node.steering} />
    case 'skill':
      return <SkillMessage name={node.name} args={node.args} body={node.body} />
    case 'assistant':
      return <AssistantNodeView node={node} canRetry={retryable} onRetry={onRetry} />
    case 'tool':
      return <ToolRowView node={node} />
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

function EmptyHero() {
  const t = useT()
  return (
    <div className={css.hero}>
      <div className={css.heroMark}>
        <HeroMark />
      </div>
      <h1 className={css.heroTitle}>{t('chat.empty.title')}</h1>
      <p className={css.heroBody}>{t('chat.empty.body')}</p>
    </div>
  )
}

function HeroMark() {
  return (
    <svg width="40" height="40" viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="1" y="1" width="16" height="16" rx="4.5" fill="var(--dsw-alias-state-business-primary)" opacity="0.16" />
      <path
        d="M9 4.2 10.3 7.7 13.8 9l-3.5 1.3L9 13.8 7.7 10.3 4.2 9l3.5-1.3Z"
        fill="var(--dsw-alias-state-business-primary)"
      />
    </svg>
  )
}
