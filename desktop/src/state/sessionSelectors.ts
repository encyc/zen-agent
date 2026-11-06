import type { SessionSummary } from '../rpc/types'

/** `session op fork` always copies the current session; offering the button on
 * another row would fork the wrong transcript. */
export function canForkSession(currentPath: string | null, sessionPath: string): boolean {
  return currentPath !== null && sessionPath === currentPath
}

/** The runtime is cwd-bound: opening a session recorded in another project
 * means restarting the runtime at that project's root first (pi's
 * `selectSession → activateProject`). Sessions with no recorded root have
 * nowhere to point it, so they stay discovery-only. */
export function isBlockedSession(
  session: Pick<SessionSummary, 'path' | 'project_path'>,
  currentPath: string | null,
): boolean {
  if (session.path === currentPath) return false
  return (session.project_path ?? '') === ''
}
