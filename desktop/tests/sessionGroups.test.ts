import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PROJECT_PAGE_SIZE,
  mergeProjects,
  projectName,
  projectRowCount,
  sessionSections,
} from '../src/state/sessionGroups.ts'
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
  const byTitle = sessionSections(sessions, 'LOGIN')
  assert.deepEqual(
    [...byTitle.pinned, ...byTitle.standalone, ...byTitle.projects.flatMap((g) => g.sessions)].map(
      (s) => s.path,
    ),
    ['/s/a.jsonl'],
  )
  const byProject = sessionSections(sessions, 'beta')
  assert.deepEqual(
    byProject.projects.flatMap((group) => group.sessions).map((s) => s.path),
    ['/s/c.jsonl'],
  )
})

test('sections split pinned, projectless and project groups', () => {
  const sections = sessionSections(
    [
      make('/s/a.jsonl', 9, { project: '' }),
      make('/s/b.jsonl', 8, { project: 'alpha' }),
      make('/s/c.jsonl', 7, { project: 'beta' }),
      make('/s/d.jsonl', 6, { project: 'beta', pinned: true }),
    ],
    '',
  )
  assert.deepEqual(sections.pinned.map((s) => s.path), ['/s/d.jsonl'])
  assert.deepEqual(sections.standalone.map((s) => s.path), ['/s/a.jsonl'])
  assert.deepEqual(sections.projects.map((g) => g.project), ['alpha', 'beta'])
})

test('each list sorts updated desc with a path tie-break and groups follow their newest row', () => {
  const sections = sessionSections(
    [
      make('/s/a.jsonl', 1, { project: '' }),
      make('/s/b.jsonl', 5, { project: 'alpha' }),
      make('/s/c.jsonl', 5, { project: 'alpha' }),
      make('/s/d.jsonl', 2, { project: 'beta' }),
    ],
    '',
  )
  assert.deepEqual(sections.standalone.map((s) => s.path), ['/s/a.jsonl'])
  assert.deepEqual(
    sections.projects.map((g) => [g.project, g.sessions.map((s) => s.path)]),
    [
      ['alpha', ['/s/b.jsonl', '/s/c.jsonl']],
      ['beta', ['/s/d.jsonl']],
    ],
  )
})

test('the query filters every section and keeps collapse data intact', () => {
  const sections = sessionSections(
    [
      make('/s/a.jsonl', 9, { project: '', title: 'Fix login' }),
      make('/s/b.jsonl', 8, { project: 'alpha', title: 'other' }),
    ],
    'login',
  )
  assert.deepEqual(sections.standalone.map((s) => s.path), ['/s/a.jsonl'])
  assert.deepEqual(sections.projects, [])
})

test('the project page size is ten rows', () => {
  assert.equal(PROJECT_PAGE_SIZE, 10)
})

test('project rows grow one page per "more" click, never past the group', () => {
  assert.equal(projectRowCount(25, 1), PROJECT_PAGE_SIZE)
  assert.equal(projectRowCount(25, 2), PROJECT_PAGE_SIZE * 2)
  assert.equal(projectRowCount(25, 3), 25)
  assert.equal(projectRowCount(25, 9), 25)
  assert.equal(projectRowCount(4, 1), 4)
  assert.equal(projectRowCount(0, 1), 0)
  // Missing or stale page state still shows the first page.
  assert.equal(projectRowCount(25, 0), PROJECT_PAGE_SIZE)
})

test('registered projects with no sessions join the rail and follow the query', () => {
  const groups = [{ project: 'beta', sessions: [make('/s/b.jsonl', 1, { project: 'beta' })] }]
  const entries = [{ path: '/w/alpha', name: 'alpha', at: 1 }]
  assert.deepEqual(
    mergeProjects(groups, entries, ''),
    [
      { project: 'beta', path: null, sessions: groups[0].sessions },
      { project: 'alpha', path: '/w/alpha', sessions: [] },
    ],
  )
  assert.deepEqual(mergeProjects(groups, entries, 'AL'), [
    { project: 'alpha', path: '/w/alpha', sessions: [] },
  ])
})

test('a registered project attaches its path to the matching session group', () => {
  const sessions = [make('/s/a.jsonl', 1, { project: 'alpha' })]
  assert.deepEqual(
    mergeProjects(
      sessionSections(sessions, '').projects,
      [{ path: '/w/alpha', name: 'alpha', at: 1 }],
      '',
    ),
    [{ project: 'alpha', path: '/w/alpha', sessions }],
  )
})

test('a session-only project group keeps its root from the session entries', () => {
  const sessions = [make('/s/b.jsonl', 1, { project: 'beta', project_path: '/w/beta' })]
  assert.deepEqual(mergeProjects(sessionSections(sessions, '').projects, [], ''), [
    { project: 'beta', path: '/w/beta', sessions },
  ])
})

test('a registered project path wins over the session-recorded root', () => {
  const sessions = [make('/s/b.jsonl', 1, { project: 'beta', project_path: '/w/old' })]
  assert.deepEqual(
    mergeProjects(
      sessionSections(sessions, '').projects,
      [{ path: '/w/beta', name: 'beta', at: 1 }],
      '',
    ),
    [{ project: 'beta', path: '/w/beta', sessions }],
  )
})

test('project names come from the last path segment', () => {
  assert.equal(projectName('/Users/me/work/zen-agent/'), 'zen-agent')
  assert.equal(projectName('C:\\work\\alpha'), 'alpha')
})
