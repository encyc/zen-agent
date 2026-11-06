import assert from 'node:assert/strict'
import { test } from 'node:test'
import { COMMAND_POLICY, nextPaletteIndex, paletteResults } from '../src/state/palette.ts'
import type { CommandEntry, SessionSummary } from '../src/rpc/types.ts'

const make = (path: string, extra: Partial<SessionSummary> = {}): SessionSummary => ({
  path,
  name: path,
  title: path,
  project: 'alpha',
  messages: 1,
  current: false,
  pinned: false,
  updated_at: 1,
  ...extra,
})

const commands: CommandEntry[] = [
  { name: 'compact', description: 'Compact context' },
  { name: 'rename', description: 'Rename session' },
  { name: 'quit', description: 'Quit' },
  { name: 'sessions', description: 'Session picker' },
]

test('sessions match title/name/project and are disabled off-project', () => {
  const hits = paletteResults(
    [
      make('/s/a.jsonl', { title: 'Fix login' }),
      make('/s/b.jsonl', { title: 'Other', project: 'beta' }),
    ],
    [],
    'fix',
    'alpha',
  )
  assert.deepEqual(hits.sessions.map((hit) => [hit.session.path, hit.disabled]), [['/s/a.jsonl', false]])

  const all = paletteResults(
    [make('/s/a.jsonl'), make('/s/b.jsonl', { project: 'beta', updated_at: 2 })],
    [],
    '',
    'alpha',
  )
  assert.deepEqual(all.sessions.map((hit) => [hit.session.path, hit.disabled]), [
    ['/s/b.jsonl', true],
    ['/s/a.jsonl', false],
  ])
})

test('only allowlisted commands surface, with run/insert modes', () => {
  const hits = paletteResults([], commands, '', 'alpha')
  assert.deepEqual(hits.commands.map((hit) => [hit.name, hit.mode]), [
    ['compact', 'run'],
    ['rename', 'insert'],
  ])
  assert.equal(COMMAND_POLICY.quit, undefined)
  assert.equal(COMMAND_POLICY.sessions, undefined)
})

test('command search matches name and description', () => {
  assert.deepEqual(paletteResults([], commands, 'RENAME', 'alpha').commands.map((hit) => hit.name), ['rename'])
  assert.deepEqual(paletteResults([], commands, 'compact', 'alpha').commands.map((hit) => hit.name), ['compact'])
})

test('nextPaletteIndex skips disabled rows and wraps', () => {
  const options = [{ disabled: false }, { disabled: true }, { disabled: false }]
  assert.equal(nextPaletteIndex(options, 0, 1), 2)
  assert.equal(nextPaletteIndex(options, 2, 1), 0)
  assert.equal(nextPaletteIndex(options, -1, 1), 0)
  assert.equal(nextPaletteIndex(options, -1, -1), 2)
  assert.equal(nextPaletteIndex(options, 0, -1), 2)
  assert.equal(nextPaletteIndex([{ disabled: true }], -1, 1), -1)
  assert.equal(nextPaletteIndex([], -1, 1), -1)
})
