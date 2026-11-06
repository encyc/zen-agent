/** `session op fork` always copies the current session; offering the button on
 * another row would fork the wrong transcript. */
export function canForkSession(currentPath: string | null, sessionPath: string): boolean {
  return currentPath !== null && sessionPath === currentPath
}
