/** Land a `status` reply: session guard, store write, run-failure notice.
 *
 * A request sent before a session switch can answer for the session that was
 * on screen then; returning false lets the caller skip the busy/usage writes
 * that would otherwise land on the wrong session. */

import type { StatusResult } from '../rpc/types.ts'
import { useSessionStore, useStatusStore } from './stores.ts'
import { surfaceRunError } from './runError.ts'

export function applyStatus(status: StatusResult): boolean {
  const active = useSessionStore.getState().currentPath
  if (
    active !== null &&
    typeof status.session === 'string' &&
    status.session !== active
  ) {
    return false
  }
  useStatusStore.getState().set(status)
  surfaceRunError(status.last_error)
  return true
}
