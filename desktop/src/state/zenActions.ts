/** Zen mode actions shared by the app store: the fleet RPC round-trips live
 * here so `appStore.ts` stays under its architecture budget. Each function
 * takes the store getter, exactly like the inline methods it replaces.
 *
 * The workspace mode owns the UI; the fleet is independent of it. Entering
 * zen enables the fleet (refusing busy slots reverts the UI), leaving zen
 * only stops the fleet when the user turned background running off. */

import { useSettingsStore, type WorkspaceMode } from './stores.ts'
import { useSessionStore } from './stores.ts'
import { useNoticeStore } from './stores.ts'
import { syncSwarmState } from './swarmWiring.ts'
import { useZenStore } from './zenStore.ts'
import type { RosterItem, ServerFrame } from '../rpc/types.ts'
import { frameErrorNotice } from '../rpc/frames.ts'

/** Surface a refusal on the notice banner: fleet actions must never fail
 * silently (a busy router is a normal operating state, not an edge). */
function showRefusal(frame: Record<string, unknown>): void {
  const text = frameErrorNotice(frame, useSettingsStore.getState().locale)
  if (text !== null) useNoticeStore.getState().show({ kind: 'error', text })
}

type AppGetter = () => {
  client(): { send(payload: Record<string, unknown>): Promise<Record<string, unknown> & { type?: string; ok?: boolean }> }
  refreshTranscript(): Promise<boolean>
  refreshSessions(): Promise<void>
  project: string | null
}

/** Consume a zen_fleet roster frame; false means "not mine, keep flowing".
 * Extracted from appStore.handleFrame to hold its LOC budget. */
export function applyFleetFrame(frame: ServerFrame): boolean {
  if (frame.type !== 'zen_fleet') return false
  useZenStore.getState().applyRoster(frame.enabled, frame.roster ?? [])
  return true
}

/** Follow an enable/disable result: apply the roster, adopt the returned
 * session (the slot moved server-side) and refresh both views. */
function applyToggleResult(
  get: AppGetter,
  enabled: boolean,
  frame: Record<string, unknown>,
): void {
  const zen = useZenStore.getState()
  const roster = Array.isArray(frame.roster) ? (frame.roster as RosterItem[]) : []
  zen.applyRoster(enabled, roster)
  if (zen.workerPath !== null) zen.closeWorker()
  const session = typeof frame.session === 'string' ? frame.session : null
  if (session) {
    useSessionStore.getState().setCurrent(session)
  }
  void get().refreshTranscript()
  void get().refreshSessions()
}

export async function switchZenPresence(get: AppGetter, entering: boolean): Promise<void> {
  if (!entering) return // leaving zen only switches the UI; workers keep running
  // Explicit re-entry is the only thing that re-arms auto-enable after a stop.
  useZenStore.setState({ stoppedByUser: false })
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send({ type: 'zen_fleet', op: 'enable' })
  } catch {
    useSettingsStore.getState().setWorkspaceMode('coding')
    return
  }
  if (frame.type !== 'result' || !frame.ok) {
    useSettingsStore.getState().setWorkspaceMode('coding')
    showRefusal(frame)
    return
  }
  applyToggleResult(get, true, frame)
}

/** Explicitly stop the fleet and restore the pre-zen session. */
export async function stopFleet(get: AppGetter): Promise<void> {
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send({ type: 'zen_fleet', op: 'disable' })
  } catch {
    return
  }
  if (frame.type !== 'result' || !frame.ok) {
    showRefusal(frame)
    return
  }
  applyToggleResult(get, false, frame)
  useZenStore.getState().markStopped()
}

/** The leave-zen transition: only stops the fleet when background running
 * is off. Changing the setting never acts on a running fleet immediately. */
export async function onZenModeLeft(get: AppGetter): Promise<void> {
  if (useSettingsStore.getState().zenBackgroundRun) return
  await stopFleet(get)
}

/** One place for the mode switcher: persist the mode, then run the zen-side
 * transitions (enable on enter, gated stop on leave). */
export function switchWorkspaceMode(get: AppGetter, mode: WorkspaceMode): void {
  const prev = useSettingsStore.getState().workspaceMode
  useSettingsStore.getState().setWorkspaceMode(mode)
  if (mode === 'zen') void switchZenPresence(get, true)
  else if (prev === 'zen') void onZenModeLeft(get)
  // Entering swarm loads the active project's room (no-op in other modes).
  if (mode === 'swarm') void syncSwarmState(get().project)
}

export async function syncZenState(get: AppGetter): Promise<void> {
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send({ type: 'zen_fleet', op: 'state' })
  } catch {
    return // a pre-zen child: the toggle just stays off
  }
  if (frame.type !== 'result' || !frame.ok) return
  const enabled = Boolean(frame.enabled)
  if (
    useSettingsStore.getState().workspaceMode === 'zen' &&
    !enabled &&
    !useZenStore.getState().stoppedByUser
  ) {
    await switchZenPresence(get, true)
    return
  }
  useZenStore.getState().applyRoster(
    enabled,
    Array.isArray(frame.roster) ? (frame.roster as RosterItem[]) : [],
  )
}

/** Re-point the per-project stores after boot or a project switch: the fleet
 * roster and the swarm room both belong to the project on screen. */
export async function syncProjectState(get: AppGetter): Promise<void> {
  await syncZenState(get)
  await syncSwarmState(get().project)
}

export async function zenTranscript(
  get: AppGetter,
  path: string,
  before?: number,
): Promise<Record<string, unknown> | null> {
  try {
    const frame = await get().client().send({
      type: 'zen_fleet',
      op: 'transcript',
      path,
      ...(before !== undefined ? { before } : {}),
    })
    if (frame.type === 'result' && frame.ok) return frame
    return null
  } catch {
    return null
  }
}
