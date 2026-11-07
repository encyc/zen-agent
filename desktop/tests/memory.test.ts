import assert from 'node:assert/strict'
import { test } from 'node:test'
import { filterMemoryEntries, sortMemoryEntries } from '../src/state/memory.ts'
import type { MemoryEntry } from '../src/rpc/types.ts'

function entry(id: string, title: string, body: string, updated = '2026-11-01T00:00:00'): MemoryEntry {
  return { id, title, body, created: updated, updated, source: 'ui' }
}

const ENTRIES = [
  entry('aaaa', 'Build commands', 'Run uv and ruff.'),
  entry('bbbb', 'Release flow', 'Tag, build, upload.'),
]

test('an empty query keeps every entry', () => {
  assert.equal(filterMemoryEntries(ENTRIES, '   '), ENTRIES)
})

test('the filter matches titles and bodies case-insensitively', () => {
  assert.deepEqual(filterMemoryEntries(ENTRIES, 'RUFF').map((item) => item.id), ['aaaa'])
  assert.deepEqual(filterMemoryEntries(ENTRIES, 'release').map((item) => item.id), ['bbbb'])
  assert.deepEqual(filterMemoryEntries(ENTRIES, 'missing'), [])
})

test('the newest entry sorts first, ties broken by id', () => {
  const sorted = sortMemoryEntries([
    entry('aaaa', 'old', '', '2026-10-01T00:00:00'),
    entry('bbbb', 'new', '', '2026-11-05T00:00:00'),
    entry('cccc', 'same', '', '2026-11-05T00:00:00'),
  ])
  assert.deepEqual(sorted.map((item) => item.id), ['cccc', 'bbbb', 'aaaa'])
})
