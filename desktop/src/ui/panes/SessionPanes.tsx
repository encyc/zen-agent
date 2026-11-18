/** The coding-mode pane strip: every open session side by side, live. The
 * strip replaces the single conversation while at least two panes are open;
 * closing back down to one returns the classic layout (App owns that switch). */

import { usePanesStore } from '../../state/sessionPanes'
import { PaneView } from './PaneView'
import css from './panes.module.css'

export function SessionPanes() {
  const panes = usePanesStore((state) => state.panes)
  return (
    <div className={css.strip} data-pane-strip>
      {panes.map((pane) => (
        <PaneView key={pane.path} pane={pane} />
      ))}
    </div>
  )
}
