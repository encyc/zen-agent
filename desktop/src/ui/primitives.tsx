/** Small shared primitives matching dsh's ui-primitives vocabulary. */

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import clsx from 'clsx'
import css from './primitives.module.css'

export function StateDot({
  state,
  size = 8,
}: {
  state: 'running' | 'ok' | 'error' | 'warning' | 'idle' | 'ongoing'
  size?: number
}) {
  return (
    <span
      className={css.stateDot}
      data-state={state}
      style={{ width: size, height: size }}
      aria-hidden
    />
  )
}

export function TextShimmer({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={clsx(css.shimmer, className)}>{children}</span>
}

export function Button({
  variant = 'outline',
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'outline' | 'ghost' | 'danger' }) {
  return (
    <button
      type="button"
      className={clsx(css.button, css[variant], className)}
      {...rest}
    >
      {children}
    </button>
  )
}

/** dsh-style tooltip: a delayed native-title fallback is not acceptable for
 * the composer, so this renders a real CSS tooltip on hover/focus. */
export function Tooltip({
  label,
  children,
  side = 'top',
}: {
  label: string
  children: ReactNode
  side?: 'top' | 'bottom'
}) {
  return (
    <span className={css.tooltipHost} data-side={side}>
      {children}
      <span className={css.tooltip} role="tooltip">{label}</span>
    </span>
  )
}

export function KeyHint({ keys }: { keys: string }) {
  return <span className={css.keyHint}>{keys}</span>
}
