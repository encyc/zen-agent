/** `session op fork` always copies the current session; offering the button on
 * another row would fork the wrong transcript. */
export function canForkSession(currentPath: string | null, sessionPath: string): boolean {
  return currentPath !== null && sessionPath === currentPath
}

/** Cross-project sessions are discovery-only: this process is cwd-bound, so
 * resuming one would open it against the wrong root. With no known current
 * session nothing can be proven same-project, so the check fails closed. */
export function isCrossProject(
  sessionPath: string,
  sessionProject: string | undefined,
  currentPath: string | null,
  currentProject: string | null,
): boolean {
  if (sessionPath === currentPath) return false
  if (currentPath === null || currentProject === null) return true
  return sessionProject !== currentProject
}
