/** Which session a project switch should land on.
 *
 * The runtime is cwd-bound, so switching projects replaces the child. Booting
 * that child fresh painted an empty conversation until the person picked a
 * session by hand; resuming the project's newest real conversation makes the
 * switch a content swap instead. Pure and fixture-tested: the store feeds it
 * the global session list and the root the switch is heading to. */

import type { SessionSummary } from '../rpc/types.ts'

export function latestSessionForProject(
  sessions: readonly SessionSummary[],
  root: string,
): string | null {
  let best: SessionSummary | null = null
  for (const session of sessions) {
    if (root === '' || session.project_path !== root) continue
    // An idle launch leaves a lazy, message-less session behind; it is not a
    // conversation to come back to.
    if (session.messages <= 0) continue
    if (best === null || (session.updated_at ?? 0) > (best.updated_at ?? 0)) best = session
  }
  return best?.path ?? null
}
