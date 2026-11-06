/** One turn's tool steps folded behind a single process row: the settled fold
 * stays collapsed, the live one opens so work in flight remains visible. */

import { useEffect, useState } from 'react'
import type { ToolNode } from '../../state/chatStore'
import { useT } from '../../i18n/locale'
import { formatDuration } from '../format'
import { ChevronRightIcon } from '../icons'
import { ToolRowView } from './ToolRowView'
import css from './TurnProcess.module.css'

export interface TurnProcessProps {
  tools: ToolNode[]
  durationMs: number | null
  running: boolean
}

export function TurnProcess({ tools, durationMs, running }: TurnProcessProps) {
  const t = useT()
  const [open, setOpen] = useState(running)

  useEffect(() => {
    setOpen(running)
  }, [running])

  const label = running
    ? t('turn.running', { steps: tools.length })
    : durationMs === null
      ? t('turn.processedShort', { steps: tools.length })
      : t('turn.processed', { duration: formatDuration(durationMs), steps: tools.length })

  return (
    <div className={css.root} data-turn-process data-running={running || undefined}>
      <button
        type="button"
        className={css.header}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={css.label}>{label}</span>
        <span className={css.chevron} data-open={open || undefined} aria-hidden>
          <ChevronRightIcon size={11} />
        </span>
      </button>
      {open && (
        <div className={css.body}>
          {tools.map((tool) => (
            <ToolRowView key={tool.id} node={tool} />
          ))}
        </div>
      )}
    </div>
  )
}
