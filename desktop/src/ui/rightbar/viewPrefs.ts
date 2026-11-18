/** Remembered view switches (tree/list, graph/list). A stale or foreign value
 * falls back to the shipped default instead of breaking the panel. */

export function readViewPref<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  try {
    const value = localStorage.getItem(key)
    return allowed.includes(value as T) ? (value as T) : fallback
  } catch {
    return fallback
  }
}

export function writeViewPref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // A locked-down webview can refuse storage; the switch still works.
  }
}
