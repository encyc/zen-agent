/** The swarm mode sidebar: the compact project switcher above the roster and
 * task index. Owns the room's project-scoped load (the room page no longer
 * loads) and the role editor, so both stay reachable with the room open. */

import { useEffect, useState } from 'react'
import { useT } from '../i18n/locale'
import type { SwarmRole } from '../rpc/types'
import { useAppStore } from '../state/appStore'
import { swarmLoadPlan, useSwarmStore } from '../state/swarmStore'
import { ProjectSwitcher } from './ProjectSwitcher'
import { SwarmRoster } from './SwarmRoster'
import { SwarmTasks } from './SwarmTasks'
import { SwarmWorks } from './SwarmWorks'
import { LibraryPicker } from './pages/LibraryPicker'
import { AgentModal } from './pages/agentEditor'
import css from './Sidebar.module.css'
import panels from './SwarmPanels.module.css'

export function SwarmSidebar() {
  const t = useT()
  const project = useAppStore((state) => state.project)
  const loaded = useSwarmStore((state) => state.loaded)
  const loadedProject = useSwarmStore((state) => state.loadedProject)
  const load = useSwarmStore((state) => state.load)
  const saveRole = useSwarmStore((state) => state.saveRole)
  const [editing, setEditing] = useState<SwarmRole | 'new' | null>(null)
  const [picking, setPicking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'roles' | 'works'>('roles')

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
        <div className={panels.tabs} role="tablist">
          <button
            type="button"
            role="tab"
            className={panels.tab}
            aria-selected={tab === 'roles'}
            data-active={tab === 'roles' || undefined}
            data-swarm-tab="roles"
            onClick={() => setTab('roles')}
          >
            {t('swarm.rolesTab')}
          </button>
          <button
            type="button"
            role="tab"
            className={panels.tab}
            aria-selected={tab === 'works'}
            data-active={tab === 'works' || undefined}
            data-swarm-tab="works"
            onClick={() => setTab('works')}
          >
            {t('swarm.worksTab')}
          </button>
        </div>
        {tab === 'roles' ? (
          <SwarmRoster
            busy={busy}
            onConfigure={setEditing}
            onCreate={() => setEditing('new')}
            onCopy={() => setPicking(true)}
          />
        ) : (
          <SwarmWorks />
        )}
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
      {picking && (
        <LibraryPicker onClose={() => setPicking(false)} onCopied={() => void load(project)} />
      )}
    </div>
  )
}