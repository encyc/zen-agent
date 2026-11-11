/** Zen mode actions shared by the app store: the fleet RPC round-trips live
 * here so `appStore.ts` stays under its architecture budget. Each function
 * takes the store getter, exactly like the inline methods it replaces. */

import { useSettingsStore } from './stores.ts'
import { useSessionStore } from './stores.ts'
import { useZenStore } from './zenStore.ts'
import type { RosterItem, ServerFrame } from '../rpc/types.ts'
import type { ZenUiMode } from '../rpc/types.ts'

type AppGetter = () => {
  client(): { send(payload: Record<string, unknown>): Promise<Record<string, unknown> & { type?: string; ok?: boolean }> }
  refreshTranscript(): Promise<boolean>
  refreshSessions(): Promise<void>
}

/** Consume a zen_fleet roster frame; false means "not mine, keep flowing".
 * Extracted from appStore.handleFrame to hold its LOC budget. */
export function applyFleetFrame(frame: ServerFrame): boolean {
  if (frame.type !== 'zen_fleet') return false
  useZenStore.getState().applyRoster(frame.enabled, frame.roster ?? [])
  return true
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
  if (useSettingsStore.getState().zenMode === 'zen' && !enabled) {
    await setZenMode(get, 'zen')
    return
  }
  useZenStore.getState().applyRoster(
    enabled,
    Array.isArray(frame.roster) ? (frame.roster as RosterItem[]) : [],
  )
}

export async function setZenMode(get: AppGetter, mode: ZenUiMode): Promise<void> {
  useSettingsStore.getState().setZenMode(mode)
  const revert = () =>
    useSettingsStore.getState().setZenMode(mode === 'zen' ? 'coding' : 'zen')
  let frame: Record<string, unknown>
  try {
    frame = await get().client().send({
      type: 'zen_fleet',
      op: mode === 'zen' ? 'enable' : 'disable',
    })
  } catch {
    revert()
    return
  }
  if (frame.type !== 'result' || !frame.ok) {
    revert()
    return
  }
  const zen = useZenStore.getState()
  const roster = Array.isArray(frame.roster) ? (frame.roster as RosterItem[]) : []
  zen.applyRoster(mode === 'zen', roster)
  if (mode === 'zen' && zen.workerPath !== null) zen.closeWorker()
  // Enable/disable moved the slot to another session server-side; the result
  // carries which one, so the UI follows it like a resume.
  const session = typeof frame.session === 'string' ? frame.session : null
  if (session) {
    useSessionStore.getState().setCurrent(session)
    void get().refreshTranscript()
  }
  void get().refreshSessions()
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
