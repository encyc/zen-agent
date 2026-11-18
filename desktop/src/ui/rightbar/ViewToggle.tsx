/** The compact tree/list (or graph/list) switch shared by Changes, the graph
 * and the commit file list — VSCode's two-icon segmented control. */

import type { ReactNode } from 'react'
import css from './ViewToggle.module.css'

export interface ViewOption<T extends string> {
  value: T
  /** Accessible name and tooltip, e.g. "Tree view". */
  label: string
  icon: ReactNode
}

export function ViewToggle<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: readonly ViewOption<T>[]
  onChange(value: T): void
  label: string
}) {
  return (
    <div className={css.root} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={css.button}
          aria-pressed={value === option.value}
          aria-label={option.label}
          title={option.label}
          onClick={() => onChange(option.value)}
        >
          {option.icon}
        </button>
      ))}
    </div>
  )
}
