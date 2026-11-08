/** Tool call card — the dsh ToolRow shape: one collapsible row with icon,
 * title, summary, state cue, duration/diff totals suffix, and a lazily
 * mounted body (diff block, terminal-ish output, or IN/OUT card). */

import { useMemo, useState } from 'react'
import clsx from 'clsx'
import type { ToolNode } from '../../state/chatStore'
import { useWorkPanelStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import {
  ActivityIcon,
  BotIcon,
  CheckIcon,
  ChevronRightIcon,
  FileIcon,
  ListTodoIcon,
  PanelRightIcon,
  SearchIcon,
  TerminalIcon,
  WarningIcon,
} from '../icons'
import { StateDot, TextShimmer } from '../primitives'
import { formatDuration } from '../format'
import { toolCue } from '../toolState'
import { CopyAction } from './TurnActions'
import css from './ToolRowView.module.css'

const DIFF_MAX_LINES = 400
const OUTPUT_MAX_CHARS = 8000

const FAMILY_ICONS: Record<string, (props: { size?: number }) => React.ReactNode> = {
  read_file: FileIcon,
  list_dir: FileIcon,
  glob: SearchIcon,
  grep: SearchIcon,
  bash: TerminalIcon,
  wait: TerminalIcon,
  write_file: FileIcon,
  edit_file: FileIcon,
  web_search: ActivityIcon,
  fetch: ActivityIcon,
  task: BotIcon,
  todo: ListTodoIcon,
}

/** The operation hue role a tool rides (--zen-op-*); unknown tools stay
 * neutral. Roles are fixed across palettes — each theme supplies its own
 * values, so the coding survives a palette switch. */
export type ToolFamily = 'read' | 'search' | 'exec' | 'write' | 'edit' | 'think' | 'task'

const TOOL_FAMILIES: Record<string, ToolFamily> = {
  read_file: 'read',
  list_dir: 'read',
  glob: 'search',
  grep: 'search',
  web_search: 'search',
  fetch: 'search',
  bash: 'exec',
  wait: 'exec',
  write_file: 'write',
  edit_file: 'edit',
  task: 'task',
  todo: 'think',
}

function titleFor(name: string): string {
  return name
}

function summaryFor(node: ToolNode): string {
  const args = node.args
  const get = (key: string): string => {
    const value = args[key]
    return typeof value === 'string' ? value : ''
  }
  switch (node.name) {
    case 'bash':
    case 'wait':
      return get('command') || node.argsPreview
    case 'read_file':
    case 'write_file':
    case 'edit_file':
    case 'list_dir':
      return get('path') || node.argsPreview
    case 'glob':
      return get('pattern') || node.argsPreview
    case 'grep':
      return get('pattern') || node.argsPreview
    case 'web_search':
      return get('query') || node.argsPreview
    case 'fetch':
      return get('url') || node.argsPreview
    case 'task':
      return get('name') || get('description') || node.argsPreview
    case 'todo':
      return String(args.op ?? '') || node.argsPreview
    default:
      return node.argsPreview
  }
}

interface DiffLine {
  kind: 'add' | 'del' | 'ctx' | 'meta'
  text: string
}

function parseDiff(diff: string): { lines: DiffLine[]; added: number; removed: number; truncated: boolean } {
  const rawLines = diff.split('\n')
  const lines: DiffLine[] = []
  let added = 0
  let removed = 0
  for (const raw of rawLines.slice(0, DIFF_MAX_LINES)) {
    if (raw.startsWith('+++') || raw.startsWith('---')) {
      lines.push({ kind: 'meta', text: raw })
    } else if (raw.startsWith('+')) {
      lines.push({ kind: 'add', text: raw.slice(1) })
      added += 1
    } else if (raw.startsWith('-')) {
      lines.push({ kind: 'del', text: raw.slice(1) })
      removed += 1
    } else if (raw.startsWith('@@')) {
      lines.push({ kind: 'meta', text: raw })
    } else {
      lines.push({ kind: 'ctx', text: raw.startsWith(' ') ? raw.slice(1) : raw })
    }
  }
  return { lines, added, removed, truncated: rawLines.length > DIFF_MAX_LINES }
}

export function ToolRowView({ node }: { node: ToolNode }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const running = node.state === 'running'
  const cue = toolCue(node.state)
  const summary = running && node.progress ? node.progress : summaryFor(node)
  const isTask = node.name === 'task'
  const taskName =
    typeof node.args.name === 'string' && node.args.name.trim() !== ''
      ? node.args.name
      : 'task'
  // A task row is a door to the work panel, not an inline IN/OUT card: the
  // child's conversation is too wide for the folded process body.
  const expandable = !running && !isTask
  const hasDiff = node.diff !== null
  const diff = useMemo(() => (hasDiff ? parseDiff(node.diff ?? '') : null), [hasDiff, node.diff])
  const output = node.resultText
    ? node.resultText.length > OUTPUT_MAX_CHARS
      ? `${node.resultText.slice(-OUTPUT_MAX_CHARS)}\n…`
      : node.resultText
    : ''
  const Icon = FAMILY_ICONS[node.name] ?? FileIcon
  const family = TOOL_FAMILIES[node.name]

  return (
    <div
      className={css.root}
      data-state={node.state}
      data-tool={node.name}
      data-family={family}
    >
      <button
        type="button"
        className={css.row}
        title={isTask ? t('panel.openSubagent') : undefined}
        onClick={() => {
          if (isTask) {
            useWorkPanelStore.getState().openSubagent({
              callId: node.callId,
              name: taskName,
            })
            return
          }
          if (expandable) setOpen((value) => !value)
        }}
        aria-expanded={isTask ? undefined : open}
      >
        <span className={css.chevron}>
          {expandable ? <ChevronRightIcon size={10} /> : null}
        </span>
        <span className={css.leading}>
          <Icon size={13} />
        </span>
        <span className={css.title}>{titleFor(node.name)}</span>
        <span className={css.summary}>
          {running ? <TextShimmer>{summary}</TextShimmer> : summary}
        </span>
        {diff !== null && node.state === 'ok' && (
          <span className={clsx(css.suffix, css.diffStat)}>
            <span className={css.added}>+{diff.added}</span>{' '}
            <span className={css.removed}>-{diff.removed}</span>
          </span>
        )}
        {isTask && (
          <span className={css.panelHint} aria-hidden>
            <PanelRightIcon size={12} />
          </span>
        )}
        {cue.kind === 'running' ? (
          <StateDot state="running" size={7} />
        ) : cue.kind === 'failed' ? (
          <span className={css.errorCue}>
            <WarningIcon size={12} />
            <span>{t(cue.label)}</span>
          </span>
        ) : cue.kind === 'stopped' ? (
          <span className={css.interruptedCue}>
            {node.durationMs !== null && node.durationMs !== undefined && (
              <span>{formatDuration(node.durationMs)}</span>
            )}
            <span>{t(cue.label)}</span>
          </span>
        ) : (
          <span className={css.okCue}>
            {node.durationMs !== null && node.durationMs !== undefined && (
              <span>{formatDuration(node.durationMs)}</span>
            )}
            <CheckIcon size={11} />
          </span>
        )}
      </button>
      {open && (
        <div className={css.bodyWrap}>
          {diff !== null && (
            <div className={css.diffWrap}>
              <div className={css.blockHead}>
                <span className={css.ioLabel}>{t('tool.diff')}</span>
                <CopyAction text={node.diff ?? ''} />
              </div>
              <div className={css.diffBlock} data-selectable>
                {diff.lines.map((line, index) => (
                  <div key={index} className={css.diffLine} data-kind={line.kind}>
                    <span className={css.diffMarker}>
                      {line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ''}
                    </span>
                    <span className={css.diffText}>{line.text || ' '}</span>
                  </div>
                ))}
                {diff.truncated && <div className={css.diffLine}>…</div>}
              </div>
            </div>
          )}
          {!hasDiff && (
            <div className={css.ioCard} data-selectable>
              {Object.keys(node.args).length > 0 && (
                <div className={css.ioSection}>
                  <div className={css.blockHead}>
                    <span className={css.ioLabel}>{t('tool.input')}</span>
                    <CopyAction text={formatArgs(node.args)} />
                  </div>
                  <pre className={css.ioText}>{formatArgs(node.args)}</pre>
                </div>
              )}
              {output !== '' && (
                <div className={css.ioSection}>
                  <div className={css.blockHead}>
                    <span className={css.ioLabel}>{t('tool.output')}</span>
                    <CopyAction text={output} />
                  </div>
                  <pre className={css.ioText} data-error={node.isError || undefined}>
                    {output}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function formatArgs(args: Record<string, unknown>): string {
  const text = JSON.stringify(args, null, 2)
  return text.length > 4000 ? `${text.slice(0, 3997)}...` : text
}
