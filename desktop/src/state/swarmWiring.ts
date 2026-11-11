/** Swarm frame wiring extracted from `appStore.handleFrame`'s body.
 *
 * Every swarm frame carries the project whose room it belongs to, so it goes
 * through the same project filter as the chat frames: a background project's
 * room must not paint the active one, and only the active project's @you
 * notices become notifications. `syncSwarmState` drops the room a switch is
 * leaving and loads the active project's snapshot. Keeping this out of
 * `appStore.ts` holds its LOC budget. This module never imports appStore. */

import { frameBelongsTo } from '../rpc/frames.ts'
import { _useSwarmSender, swarmNoticeText, useSwarmStore } from './swarmStore.ts'
import { useNotificationStore, useSettingsStore } from './stores.ts'
import type { ServerFrame } from '../rpc/types.ts'

/** Wire the app's client into the swarm store's request seam, so appStore
 * holds one registration line instead of the shape. */
export function registerSwarmSender(client: {
  send(request: Record<string, unknown>): Promise<Record<string, unknown>>
}): void {
  _useSwarmSender({ send: (request) => client.send(request) })
}

/** Consume a swarm frame for the active project; false means "not mine,
 * keep flowing". Untagged frames pass (a v2 server has no project tags). */
export function applySwarmFrame(
  frame: ServerFrame,
  active: string | null = null,
): boolean {
  const tagged = (frame as { swarm?: boolean }).swarm === true
  const notice = swarmNoticeText(frame)
  if (notice === null && !tagged) return false
  if (!frameBelongsTo(frame, active)) return false
  if (notice !== null) {
    useNotificationStore.getState().push({ tone: 'info', text: notice })
    return true
  }
  useSwarmStore.getState().applyFrame(frame)
  return true
}

/** Re-point the room store at the active project: clear the room it is
 * leaving, then load the active project's snapshot (the client stamps the
 * request with its project). A no-op outside swarm mode. */
export async function syncSwarmState(): Promise<void> {
  if (useSettingsStore.getState().workspaceMode !== 'swarm') return
  useSwarmStore.getState().reset()
  await useSwarmStore.getState().load()
}
