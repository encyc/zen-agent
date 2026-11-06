export function armDelete(current: string | null, path: string): string | null {
  return current === path ? null : path
}

export function deleteArmed(current: string | null, path: string): boolean {
  return current === path
}
