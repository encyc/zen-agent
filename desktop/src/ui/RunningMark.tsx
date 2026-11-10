/** The yin-yang mark as a running indicator: the disc spins while a turn is live. */

import runningMark from '../assets/running-mark.png'
import css from './RunningMark.module.css'

export function RunningMark({ size = 14 }: { size?: number }) {
  return (
    <img
      className={css.mark}
      src={runningMark}
      alt=""
      width={size}
      height={size}
      draggable={false}
      aria-hidden
      data-running-logo
    />
  )
}
