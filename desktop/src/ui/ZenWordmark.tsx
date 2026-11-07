/** Monoline ZEN logotype: geometric strokes, wide tracking, inherits text color. */

import clsx from 'clsx'
import css from './ZenWordmark.module.css'

const VIEWBOX_WIDTH = 90
const VIEWBOX_HEIGHT = 24

export function ZenWordmark({
  height = 20,
  className,
}: {
  height?: number
  className?: string
}) {
  return (
    <svg
      className={clsx(css.mark, className)}
      viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
      width={(height * VIEWBOX_WIDTH) / VIEWBOX_HEIGHT}
      height={height}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="square"
      strokeLinejoin="miter"
      role="img"
      aria-label="ZEN"
      data-zen-wordmark
    >
      <path d="M2 4h18L2 20h18" />
      <path d="M38 4v16M38 4h16M38 12h13M38 20h16" />
      <path d="M72 20V4l16 16V4" />
    </svg>
  )
}
