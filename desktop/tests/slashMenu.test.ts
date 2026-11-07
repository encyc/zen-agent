import assert from 'node:assert/strict'
import { test } from 'node:test'
import { slashEntries } from '../src/state/slashMenu.ts'

const commands = [
  { name: 'compact', description: 'Compact context' },
  { name: 'model', description: 'Pick a model' },
  { name: 'rename', description: 'Rename session' },
]

const skills = [
  { name: 'review', description: 'Review a diff', enabled: true },
  { name: 'research', description: 'Gather sources', enabled: true },
  { name: 'tidy', description: 'Tidy a folder', enabled: true },
]

test('a bare slash lists every command and every enabled skill', () => {
  const entries = slashEntries(commands, skills, '/')
  assert.deepEqual(
    entries.map((entry) => entry.name),
    ['compact', 'model', 'rename', 'skill:research', 'skill:review', 'skill:tidy'],
  )
  assert.equal(entries.find((entry) => entry.name === 'skill:review')?.kind, 'skill')
  assert.equal(entries.find((entry) => entry.name === 'compact')?.kind, 'command')
})

test('typing a name prefix filters commands and skills together', () => {
  assert.deepEqual(
    slashEntries(commands, skills, '/re').map((entry) => entry.name),
    ['rename', 'skill:research', 'skill:review'],
  )
})

test('the skill namespace filters by the skill name itself', () => {
  assert.deepEqual(
    slashEntries(commands, skills, '/skill:re').map((entry) => entry.name),
    ['skill:research', 'skill:review'],
  )
})

test('the start of the namespace falls back to every skill', () => {
  assert.deepEqual(
    slashEntries(commands, skills, '/sk').map((entry) => entry.name),
    ['skill:research', 'skill:review', 'skill:tidy'],
  )
})

test('skills switched off in settings are never suggested', () => {
  const disabled = skills.map((skill) => ({
    ...skill,
    enabled: skill.name !== 'tidy',
  }))
  assert.deepEqual(
    slashEntries(commands, disabled, '/').map((entry) => entry.name),
    ['compact', 'model', 'rename', 'skill:research', 'skill:review'],
  )
})

test('the menu closes once the token is followed by whitespace', () => {
  assert.deepEqual(slashEntries(commands, skills, '/model '), [])
  assert.deepEqual(slashEntries(commands, skills, '/model sonnet'), [])
  assert.deepEqual(slashEntries(commands, skills, 'ask /re'), [])
})

test('no entries without a leading slash or across lines', () => {
  assert.deepEqual(slashEntries(commands, skills, 'model'), [])
  assert.deepEqual(slashEntries(commands, skills, '/mo\ndel'), [])
})

test('an unknown prefix leaves the menu empty', () => {
  assert.deepEqual(slashEntries(commands, skills, '/zzz'), [])
})
