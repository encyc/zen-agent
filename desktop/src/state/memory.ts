import type { MemoryEntry } from '../rpc/types'

export function sortMemoryEntries(entries: MemoryEntry[]): MemoryEntry[] {
  return [...entries].sort(
    (left, right) =>
      right.updated.localeCompare(left.updated) || right.id.localeCompare(left.id),
  )
}

export function filterMemoryEntries(entries: MemoryEntry[], query: string): MemoryEntry[] {
  const needle = query.trim().toLowerCase()
  if (needle === '') return entries
  return entries.filter(
    (entry) =>
      entry.title.toLowerCase().includes(needle) ||
      entry.body.toLowerCase().includes(needle),
  )
}
