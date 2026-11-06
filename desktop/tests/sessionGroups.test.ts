import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sessionGroups } from '../src/state/sessionGroups.ts'
import type { SessionSummary } from '../src/rpc/types.ts'

const make = (path: string, updated: number, extra: Partial<SessionSummary> = {}): SessionSummary => ({
  path,
  name: path.slice(path.lastIndexOf('/') + 1, -6),
  title: path,
  project: 'alpha',
  messages: 1,
  current: false,
  pinned: false,
  updated_at: updated,
  ...extra,
})

test('query filters title, name and project case-insensitively', () => {
  const sessions = [
    make('/s/a.jsonl', 3, { title: 'Fix Login' }),
    make('/s/b.jsonl', 2, { title: 'other', name: 'b' }),
    make('/s/c.jsonl', 1, { title: 'other', project: 'Beta' }),
  ]
  const byTitle = sessionGroups(sessions, 'project', 'LOGIN')
  assert.deepEqual(
    [...byTitle.pinned, ...byTitle.groups.flatMap((group) => group.sessions)].map((s) => s.path),
    ['/s/a.jsonl'],
  )
  const byProject = sessionGroups(sessions, 'all', 'beta')
  assert.deepEqual(
    byProject.groups.flatMap((group) => group.sessions).map((s) => s.path),
    ['/s/c.jsonl'],
  )
})

test('pinned rows come first and each list sorts updated desc with a path tie-break', () => {
  const sections = sessionGroups(
    [
      make('/s/a.jsonl', 1),
      make('/s/b.jsonl', 5),
      make('/s/c.jsonl', 5),
      make('/s/d.jsonl', 2, { pinned: true }),
    ],
    'project',
    '',
  )
  assert.deepEqual(sections.pinned.map((s) => s.path), ['/s/d.jsonl'])
  assert.deepEqual(sections.groups[0].sessions.map((s) => s.path), ['/s/b.jsonl', '/s/c.jsonl', '/s/a.jsonl'])
})

test('project scope collapses to one group; all scope splits groups by newest session', () => {
  const sessions = [
    make('/s/a.jsonl', 9, { project: 'alpha' }),
    make('/s/b.jsonl', 4, { project: 'beta' }),
    make('/s/c.jsonl', 7, { project: 'beta' }),
  ]
  const project = sessionGroups(sessions, 'project', '')
  assert.deepEqual(project.groups.map((g) => g.project), ['alpha'])
  assert.deepEqual(project.groups[0].sessions.map((s) => s.path), ['/s/a.jsonl', '/s/c.jsonl', '/s/b.jsonl'])

  const all = sessionGroups(sessions, 'all', '')
  assert.deepEqual(all.groups.map((g) => g.project), ['alpha', 'beta'])
  assert.deepEqual(all.groups[1].sessions.map((s) => s.path), ['/s/c.jsonl', '/s/b.jsonl'])
})

test('sessions without a project group under an empty label and empty groups drop out', () => {
  const all = sessionGroups([make('/s/a.jsonl', 1, { project: '' })], 'all', '')
  assert.deepEqual(
    all.groups.map((group) => [group.project, group.sessions.map((session) => session.path)]),
    [['', ['/s/a.jsonl']]],
  )
  assert.deepEqual(sessionGroups([], 'all', ''), { pinned: [], groups: [] })
})
