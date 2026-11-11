/** The swarm mode sidebar: the compact project switcher above the roster and
 * task index. Owns the room's project-scoped load (the room page no longer
 * loads) and the role editor, so both stay reachable with the room open. */

import { useEffect, useState } from 'react'
import type { SwarmRole } from '../rpc/types'
import { useAppStore } from '../state/appStore'
import { swarmLoadPlan, useSwarmStore } from '../state/swarmStore'
import { ProjectSwitcher } from './ProjectSwitcher'
import { SwarmRoster } from './SwarmRoster'
import { SwarmTasks } from './SwarmTasks'
import { AgentModal } from './pages/agentEditor'
import css from './Sidebar.module.css'
import panels from './SwarmPanels.module.css'

export function SwarmSidebar() {
  const project = useAppStore((state) => state.project)
  const loaded = useSwarmStore((state) => state.loaded)
  const loadedProject = useSwarmStore((state) => state.loadedProject)
  const load = useSwarmStore((state) => state.load)
  const saveRole = useSwarmStore((state) => state.saveRole)
  const [editing, setEditing] = useState<SwarmRole | 'new' | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const plan = swarmLoadPlan(loaded, loadedProject, project)
    if (plan === 'none') return
    if (plan === 'switch') useSwarmStore.getState().clearRoomUi()
    void useSwarmStore.getState().load(project)
  }, [project, loaded, loadedProject])

  async function save(
    action: 'create' | 'update',
    id: string,
    manifest: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    setBusy(true)
    try {
      const answer = await saveRole(action, id, manifest)
      if (answer.ok === true) {
        setEditing(null)
        // Refresh the room without clearing the open task detail.
        await load(project)
      }
      return answer
    } catch (cause) {
      return { ok: false, message: cause instanceof Error ? cause.message : String(cause) }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.swarmBody} data-swarm-sidebar>
      <ProjectSwitcher />
      <div className={panels.side}>
        <SwarmRoster busy={busy} onConfigure={setEditing} onCreate={() => setEditing('new')} />
        <SwarmTasks />
      </div>
      {editing !== null && (
        <AgentModal
          row={editing === 'new' ? null : editing}
          busy={busy}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      )}
    </div>
  )
}