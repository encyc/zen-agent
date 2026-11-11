/** The zen sidebar body: the compact project switcher above the fleet roster.
 * The roster owns its paused banner and resume affordance (see ZenRoster). */

import { ProjectSwitcher } from './ProjectSwitcher'
import { ZenRoster } from './ZenRoster'
import css from './Sidebar.module.css'

export function ZenSidebar() {
  return (
    <div className={css.zenBody} data-zen-sidebar>
      <ProjectSwitcher />
      <ZenRoster />
    </div>
  )
}