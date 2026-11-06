/** Auxiliary transcript rows: notices (compaction divider / info), slash
 * command cards (dsh GenericCommandCard shape) and per-turn usage rows. */

import type { Usage } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { InfoIcon, SparkIcon } from '../icons'
import css from './AuxRows.module.css'

export function NoticeRow({
  variant,
  text,
  tokensBefore,
}: {
  variant: 'divider' | 'info'
  text: string
  tokensBefore?: number | null
}) {
  const t = useT()
  if (variant === 'divider') {
    const label =
      tokensBefore !== null && tokensBefore !== undefined
        ? `${t('compaction.divider')} · ${tokensBefore.toLocaleString()} ${t('usage.tokens')}`
        : text
    return (
      <div className={css.divider}>
        <span className={css.dividerLine} aria-hidden />
        <span className={css.dividerText}>{label}</span>
        <span className={css.dividerLine} aria-hidden />
      </div>
    )
  }
  return (
    <div className={css.infoRow}>
      <InfoIcon size={12} />
      <span>{text}</span>
    </div>
  )
}

export function CommandCard({
  name,
  argument,
  message,
}: {
  name: string
  argument: string
  message: string | null
}) {
  return (
    <div className={css.commandCard} data-selectable>
      <div className={css.commandHead}>
        <span className={css.commandGlyph} aria-hidden>
          <SparkIcon size={11} />
        </span>
        <span className={css.commandName}>/{name}</span>
        {argument !== '' && <span className={css.commandArg}>{argument}</span>}
      </div>
      {message !== null && <div className={css.commandMessage}>{message}</div>}
    </div>
  )
}

export function UsageRow({
  usage,
  turnMs,
  termination,
}: {
  usage: Usage | null
  turnMs: number | null
  termination: string | null
}) {
  const t = useT()
  if (usage === null && termination === null) return null
  const parts: string[] = []
  if (usage !== null) {
    parts.push(`${usage.input + usage.cache_read} in`)
    parts.push(`${usage.output} out`)
    if (usage.cost > 0) parts.push(`$${usage.cost.toFixed(4)}`)
  }
  if (turnMs !== null && turnMs > 0) {
    parts.push(turnMs < 1000 ? `${turnMs}ms` : `${(turnMs / 1000).toFixed(1)}s`)
  }
  if (termination !== null && termination !== 'stop') {
    parts.push(termination)
  }
  return (
    <div className={css.usageRow}>
      <span className={css.usageLabel}>{t('usage.turn')}</span>
      <span className={css.usageParts}>{parts.join(' · ')}</span>
    </div>
  )
}
