/** One zen worker's real conversation in the center column.

The worker keeps running in the background; this view is read-only and is
replaced by the router's ChatView when the person goes back. The rows are the
same transcript components coding and subagents use, so markdown, thinking,
tool cards and diffs all render normally. */

import { useEffect, useMemo, useRef, useState } from 'react'

import { useAppStore } from '../state/appStore'
import { useZenStore } from '../state/zenStore'
import { nodesFromViews, type ChatNode } from '../state/chatStore'
import type { DividerView, MessageView, ToolCallView } from '../rpc/types'
import { useT } from '../i18n/locale'
import { ArrowLeftIcon } from './icons'
import { AssistantNodeView } from './chat/AssistantNodeView'
import { UserMessage, SkillMessage } from './chat/MessageItem'
import { ToolRowView } from './chat/ToolRowView'
import { NoticeRow } from './chat/AuxRows'
import css from './WorkerConversation.module.css'

type View = MessageView | ToolCallView | DividerView
const POLL_MS = 1200

export function WorkerConversation() {
  const t = useT()
  const workerPath = useZenStore((state) => state.workerPath)
  const roster = useZenStore((state) => state.roster)
  const closeWorker = useZenStore((state) => state.closeWorker)
  const zenTranscript = useAppStore((state) => state.zenTranscript)
  const [views, setViews] = useState<View[] | null>(null)
  const [failed, setFailed] = useState(false)
  const scroller = useRef<HTMLDivElement | null>(null)

  const row = workerPath === null ? undefined : roster.find((entry) => entry.path === workerPath)
  const active = row?.status === 'working' || row?.status === 'queued'

  useEffect(() => {
    if (workerPath === null) return
    let alive = true
    setViews(null)
    setFailed(false)

    const load = async () => {
      const frame = await zenTranscript(workerPath)
      if (!alive) return
      if (frame === null) {
        setFailed(true)
        return
      }
      const rows = Array.isArray(frame.transcript) ? (frame.transcript as View[]) : []
      setViews(rows)
    }

    void load()
    if (!active) return () => { alive = false }
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [workerPath, active, zenTranscript])

  const nodes = useMemo(() => nodesFromViews(views ?? []), [views])

  useEffect(() => {
    const node = scroller.current
    if (node !== null) node.scrollTop = node.scrollHeight
  }, [nodes.length])

  if (workerPath === null) return null

  const title = row?.title || workerPath.split('/').pop() || workerPath
  const status = row?.status ?? 'idle'
  return (
    <div className={css.root} data-zen-worker>
      <header className={css.header} data-zen-worker-header>
        <button
          type="button"
          className={css.back}
          aria-label={t('zen.backToRouter')}
          data-zen-worker-back
          onClick={closeWorker}
        >
          <ArrowLeftIcon size={14} />
        </button>
        <div className={css.identity}>
          <span className={css.title} title={title}>
            {title}
          </span>
          {row?.focus !== undefined && row.focus !== '' && row.focus !== title && (
            <span className={css.focus} title={row.focus}>
              {row.focus}
            </span>
          )}
        </div>
        <span className={css.state} data-state={status}>
          {t(`zen.status.${status}`)}
        </span>
      </header>
      <div
        ref={scroller}
        className={css.scroll}
        role="log"
        aria-label={t('zen.workerTranscript')}
        data-zen-worker-transcript
      >
        <div className={css.rows}>
          {failed ? (
            <p className={css.empty}>{t('zen.drawerFailed')}</p>
          ) : views === null ? (
            <p className={css.empty}>{t('zen.workerLoading')}</p>
          ) : nodes.length === 0 ? (
            <p className={css.empty}>{t('zen.workerEmpty')}</p>
          ) : (
            nodes.map((node) => <TranscriptRow key={node.id} node={node} />)
          )}
        </div>
      </div>
      <footer className={css.footer}>{t('zen.workerReadonly')}</footer>
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
    case 'notice':
      return (
        <NoticeRow
          variant={node.variant}
          text={node.text}
          tokensBefore={node.tokensBefore}
        />
      )
    default:
      return null
  }
}
