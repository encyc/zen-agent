/** Vertical indent rails for a tree row. The row owns the left padding; this
 * overlay draws one hairline per ancestor level so deep paths stay scannable. */

import css from './IndentGuides.module.css'

export function IndentGuides({ depth }: { depth: number }) {
  if (depth <= 0) return null
  return (
    <span
      className={css.guides}
      style={{ width: depth * 12 }}
      aria-hidden="true"
    />
  )
}
