/** File-type accent for work-panel trees: a short extension label in a static
 * tone, or the generic file glyph for unknown types. The caller owns the
 * fixed-width slot so file and folder names stay on one column. */

import { FileIcon } from '../icons'
import { fileBadge } from './fileBadge.ts'
import css from './FileTypeBadge.module.css'

export function FileTypeBadge({ path, size = 14 }: { path: string; size?: number }) {
  const badge = fileBadge(path)
  if (badge === null) return <FileIcon size={size} />
  return (
    <span className={css.badge} data-tone={badge.tone} aria-hidden="true">
      {badge.label}
    </span>
  )
}
