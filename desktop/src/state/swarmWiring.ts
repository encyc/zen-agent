/** Swarm frame wiring extracted from `appStore.handleFrame`'s head.
 *
 * Swarm frames carry no project or session tag — the room spans every
 * resident slot — so they must be consumed before the project/session
 * filters would drop them. @you notices surface as info notifications;
 * tagged room frames land in the swarm store. Keeping this out of
 * `appStore.ts` holds its LOC budget. This module never imports appStore. */

import { _useSwarmSender, swarmNoticeText, useSwarmStore } from './swarmStore.ts'
import { useNotificationStore } from './stores.ts'
import type { ServerFrame } from '../rpc/types.ts'

/** Wire the app's client into the swarm store's request seam, so appStore
 * holds one registration line instead of the shape. */
export function registerSwarmSender(client: {
  send(request: Record<string, unknown>): Promise<Record<string, unknown>>
}): void {
  _useSwarmSender({ send: (request) => client.send(request) })
}

/** Consume a global swarm frame; false means "not mine, keep flowing". */
export function applySwarmFrame(frame: ServerFrame): boolean {
  const notice = swarmNoticeText(frame)
  if (notice !== null) {
    useNotificationStore.getState().push({ tone: 'info', text: notice })
    return true
  }
  if ((frame as { swarm?: boolean }).swarm) {
    useSwarmStore.getState().applyFrame(frame)
    return true
  }
  return false
}
