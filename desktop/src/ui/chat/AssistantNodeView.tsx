/** Assistant turn: reasoning row (dsh ReasoningRow shape — collapsed summary
 * with shimmer while streaming) + markdown body with copy affordances. */

import { useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react'
import clsx from 'clsx'
import type { AssistantNode } from '../../state/chatStore'
import { useSettingsStore, useWorkPanelStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { copyText } from '../clipboard'
import { ChevronRightIcon, SparkIcon } from '../icons'
import { TextShimmer } from '../primitives'
import { formatDuration } from '../format'
import { useMarkdown } from './markdown'
import { externalLinkAction } from './markdownLinks'
import { openExternal } from '../../rpc/client'
import { TurnActions } from './TurnActions'
import css from './AssistantNodeView.module.css'

export function AssistantNodeView({
  node,
  canRetry = false,
  onRetry,
  anchor,
}: {
  node: AssistantNode
  canRetry?: boolean
  onRetry?: () => void
  anchor?: string
}) {
  const t = useT()
  const showThinking = useSettingsStore((state) => state.showThinking)
  const autoCollapseThinking = useSettingsStore((state) => state.autoCollapseThinking)
  const html = useMarkdown(
    node.text,
    { copy: t('chat.copy'), copied: t('chat.copied') },
    { fileLinks: true },
  )
  const hasThinking = node.thinking !== null && node.thinking.trim() !== ''
  const duration = node.durationMs !== null ? formatDuration(node.durationMs) : ''

  // A file reference opens the rail's Files tab; code blocks are injected
  // markup, so copy buttons and file refs are handled by delegation rather
  // than a React listener per block.
  const fileRefAt = (target: EventTarget | null): string | null => {
    if (!(target instanceof HTMLElement)) return null
    return target.closest<HTMLElement>('[data-file-path]')?.dataset.filePath ?? null
  }

  const onMarkdownClick = (event: MouseEvent<HTMLDivElement>) => {
    const path = fileRefAt(event.target)
    if (path !== null) {
      event.preventDefault()
      useWorkPanelStore.getState().openFile(path)
      return
    }
    const target = event.target
    if (!(target instanceof HTMLElement)) return
    // Model-markdown links never navigate this window: http(s) goes to the
    // system browser, anything else is dropped (audit P2).
    const anchor = target.closest<HTMLAnchorElement>('a[href]')
    if (anchor !== null) {
      const href = anchor.getAttribute('href')
      const action = externalLinkAction(href)
      if (action !== null) {
        event.preventDefault()
        if (action === 'browser' && href !== null) {
          void openExternal(href).catch(() => {})
        }
        return
      }
    }
    const button = target.closest<HTMLElement>('[data-copy-code]')
    if (button === null) return
    const code = button.closest('.md-code-block')?.querySelector('code')
    if (code === null || code === undefined) return
    void copyText(code.textContent ?? '').then((ok) => {
      if (!ok) return
      button.dataset.copied = 'true'
      window.setTimeout(() => {
        delete button.dataset.copied
      }, 1500)
    })
  }

  const onMarkdownKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter') return
    const path = fileRefAt(event.target)
    if (path === null) return
    event.preventDefault()
    useWorkPanelStore.getState().openFile(path)
  }

  return (
    <div
      className={css.root}
      data-streaming={node.streaming || undefined}
      data-turn-row
      data-minimap-id={anchor}
    >
      {showThinking && hasThinking ? (
        <ThinkingRow
          text={node.thinking ?? ''}
          running={node.thinkingRunning}
          autoCollapse={autoCollapseThinking}
        />
      ) : node.thinkingRunning ? (
        <ThinkingLive />
      ) : null}
      {node.text !== '' && (
        <div
          className={css.markdown}
          data-selectable
          onClick={onMarkdownClick}
          onKeyDown={onMarkdownKeyDown}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
      {duration !== '' && !node.streaming && (
        <div className={css.meta}>{duration}</div>
      )}
      {!node.streaming && (
        <TurnActions text={node.text} canRetry={canRetry} onRetry={onRetry} />
      )}
    </div>
  )
}

function ThinkingRow({
  text,
  running,
  autoCollapse,
}: {
  text: string
  running: boolean
  autoCollapse: boolean
}) {
  const t = useT()
  const [open, setOpen] = useState(running || !autoCollapse)
  const summary = useMemo(() => firstLine(text) || t('thinking.label'), [text, t])
  // A live row stays open; a settled row follows the preference.
  useEffect(() => {
    setOpen(running || !autoCollapse)
  }, [running, autoCollapse])
  return (
    <div className={css.thinking} data-open={open || undefined} data-testid="thinking-row">
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
        <div className={css.thinkingBody} data-selectable data-testid="thinking-body">
          {text}
        </div>
      )}
    </div>
  )
}

/** Hidden reasoning keeps a live indicator, but no settled excerpts. */
function ThinkingLive() {
  const t = useT()
  return (
    <div className={css.thinking} data-testid="thinking-live" role="status">
      <span className={css.thinkingHead}>
        <span className={css.thinkingTitle}>
          <TextShimmer>{t('thinking.label')}…</TextShimmer>
        </span>
        <span className={css.thinkingIcon} aria-hidden>
          <SparkIcon size={11} />
        </span>
      </span>
    </div>
  )
}

function firstLine(text: string): string {
  const line = text.split('\n', 1)[0] ?? ''
  return line.length > 96 ? `${line.slice(0, 93)}…` : line
}
