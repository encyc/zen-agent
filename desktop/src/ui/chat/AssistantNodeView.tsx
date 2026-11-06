/** Assistant turn: reasoning row (dsh ReasoningRow shape — collapsed summary
 * with shimmer while streaming) + markdown body. */

import { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import type { AssistantNode } from '../../state/chatStore'
import { useT } from '../../i18n/locale'
import { ChevronRightIcon, SparkIcon } from '../icons'
import { TextShimmer } from '../primitives'
import { formatDuration } from '../format'
import { useMarkdown } from './MessageItem'
import css from './AssistantNodeView.module.css'

export function AssistantNodeView({ node }: { node: AssistantNode }) {
  const html = useMarkdown(node.text)
  const hasThinking = node.thinking !== null && node.thinking.trim() !== ''
  const meta = [
    node.model,
    node.durationMs !== null ? formatDuration(node.durationMs) : '',
  ].filter((part) => part !== '')
  return (
    <div className={css.root} data-streaming={node.streaming || undefined}>
      {hasThinking && (
        <ThinkingRow
          text={node.thinking ?? ''}
          running={node.thinkingRunning}
        />
      )}
      {node.text !== '' && (
        <div
          className={css.markdown}
          data-selectable
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
      {meta.length > 0 && !node.streaming && (
        <div className={css.meta}>{meta.join(' · ')}</div>
      )}
    </div>
  )
}

function ThinkingRow({ text, running }: { text: string; running: boolean }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const summary = useMemo(() => firstLine(text) || t('thinking.label'), [text, t])
  // Open automatically while a reasoning block streams, close when done.
  useEffect(() => {
    if (running) setOpen(true)
  }, [running])
  return (
    <div className={css.thinking} data-open={open || undefined}>
      <button
        type="button"
        className={css.thinkingHead}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className={clsx(css.chevron, running && css.chevronRunning)}>
          <ChevronRightIcon size={11} />
        </span>
        <span className={css.thinkingTitle}>
          {running ? <TextShimmer>{t('thinking.label')}…</TextShimmer> : t('thinking.label')}
        </span>
        <span className={css.thinkingSummary}>
          {running ? <TextShimmer>{summary}</TextShimmer> : summary}
        </span>
        <span className={css.thinkingIcon} aria-hidden>
          <SparkIcon size={11} />
        </span>
      </button>
      {open && (
        <div className={css.thinkingBody} data-selectable>
          {text}
        </div>
      )}
    </div>
  )
}

function firstLine(text: string): string {
  const line = text.split('\n', 1)[0] ?? ''
  return line.length > 96 ? `${line.slice(0, 93)}…` : line
}
