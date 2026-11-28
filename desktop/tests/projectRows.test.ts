import assert from 'node:assert/strict'
import { test } from 'node:test'
import { projectRows } from '../src/state/projectRows.ts'

const session = (root: string, updated: number) => ({
  project_path: root,
  updated_at: updated,
})

const archived = (root: string) => ({ project_path: root })

test('session-only projects join the page with their counts', () => {
  assert.deepEqual(
    projectRows([session('/w/p', 3), session('/w/p', 7)], []),
    [
      {
        path: '/w/p',
        root: '/w/p',
        name: 'p',
        registered: false,
        stat: { sessions: 2, last: 7 },
        archived: 0,
      },
    ],
  )
})

test('a registered folder inside the root marks the row as registered', () => {
  assert.deepEqual(
    projectRows([session('/w/p', 3)], [
      { path: '/w/p/src', name: 'p-src', at: 1 },
    ]),
    [
      {
        path: '/w/p/src',
        root: '/w/p',
        name: 'p-src',
        registered: true,
        stat: { sessions: 1, last: 3 },
        archived: 0,
      },
    ],
  )
})

test('registered folders without sessions are listed after active projects', () => {
  assert.deepEqual(
    projectRows([session('/w/p', 5)], [{ path: '/w/empty', name: 'empty', at: 1000 }]),
    [
      {
        path: '/w/p',
        root: '/w/p',
        name: 'p',
        registered: false,
        stat: { sessions: 1, last: 5 },
        archived: 0,
      },
      {
        path: '/w/empty',
        root: '/w/empty',
        name: 'empty',
        registered: true,
        stat: { sessions: 0, last: 0 },
        archived: 0,
      },
    ],
  )
})

test('two registered folders of one repository collapse into one row', () => {
  const rows = projectRows([session('/w/p', 1)], [
    { path: '/w/p/src', name: 'src', at: 1 },
    { path: '/w/p/plugins', name: 'plugins', at: 1 },
  ])
  assert.equal(rows.length, 1)
  assert.equal(rows[0]?.root, '/w/p')
})

test('a registered parent folder stays its own row', () => {
  const rows = projectRows([session('/w/p', 1)], [{ path: '/w', name: 'w', at: 1 }])
  assert.deepEqual(
    rows.map((row) => [row.path, row.registered]),
    [
      ['/w/p', false],
      ['/w', true],
    ],
  )
})

test('legacy sessions without a project path are ignored', () => {
  assert.deepEqual(projectRows([session('', 9)], []), [])
})

test('rows sort by last activity, newest first', () => {
  const rows = projectRows([session('/w/old', 2), session('/w/new', 8)], [])
  assert.deepEqual(
    rows.map((row) => row.name),
    ['new', 'old'],
  )
})

test('archived sessions count per project without entering the active stats', () => {
  const rows = projectRows(
    [session('/w/p', 7)],
    [],
    [archived('/w/p'), archived('/w/p'), archived('/w/q')],
  )
  const p = rows.find((row) => row.root === '/w/p')
  const q = rows.find((row) => row.root === '/w/q')
  assert.equal(p?.archived, 2)
  assert.equal(p?.stat.sessions, 1, 'archived rows never inflate the active count')
  assert.equal(q?.archived, 1)
})

test('archived rows without a project path are ignored', () => {
  const rows = projectRows([], [], [archived('')])
  assert.deepEqual(rows, [])
})

test('a project with only archived sessions still gets a row', () => {
  const rows = projectRows([], [], [archived('/w/p')])
  assert.equal(rows.length, 1)
  assert.equal(rows[0]?.archived, 1)
  assert.equal(rows[0]?.stat.sessions, 0)
  assert.equal(rows[0]?.stat.last, 0)
})
