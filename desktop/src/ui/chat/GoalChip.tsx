/** One-line goal status above the composer; opens the details panel. */

import type { GoalView } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { goalStatusIcon, goalSummary } from './goalView'
import css from './GoalChip.module.css'

export function GoalChip(props: { view: GoalView; onOpen(): void }) {
  const t = useT()
  return (
    <button
      type="button"
      className={css.chip}
      data-status={props.view.status}
      aria-label={t('goal.chip.open')}
      onClick={props.onOpen}
    >
      <span className={css.icon}>{goalStatusIcon(props.view.status)}</span>
      <span className={css.label}>{t('goal.chip.label')}</span>
      <span className={css.objective}>{goalSummary(props.view)}</span>
    </button>
  )
}
