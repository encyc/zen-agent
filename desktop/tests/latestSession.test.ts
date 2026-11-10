import test from 'node:test'
import assert from 'node:assert/strict'
import { latestSessionForProject } from '../src/state/latestSession.ts'
import type { SessionSummary } from '../src/rpc/types.ts'

function row(overrides: Partial<SessionSummary> & Pick<SessionSummary, 'path'>): SessionSummary {
  return {
    name: 's',
    title: '会话',
    project: 'p',
    project_path: '/work/p',
    messages: 2,
    current: false,
    pinned: false,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  }
}

test('latestSessionForProject picks the newest conversation in that project', () => {
  const sessions = [
    row({ path: '/sessions/a.jsonl', updated_at: 40 }),
    row({ path: '/sessions/b.jsonl', updated_at: 90 }),
    row({ path: '/sessions/c.jsonl', project_path: '/work/q', updated_at: 120 }),
  ]
  assert.equal(latestSessionForProject(sessions, '/work/p'), '/sessions/b.jsonl')
})

test('latestSessionForProject ignores sessions that never held a message', () => {
  const sessions = [
    row({ path: '/sessions/empty.jsonl', messages: 0, updated_at: 500 }),
    row({ path: '/sessions/real.jsonl', updated_at: 10 }),
  ]
  assert.equal(latestSessionForProject(sessions, '/work/p'), '/sessions/real.jsonl')
})

test('latestSessionForProject returns null when the project has nothing to resume', () => {
  assert.equal(latestSessionForProject([], '/work/p'), null)
  assert.equal(latestSessionForProject([row({ path: '/sessions/c.jsonl', project_path: '/work/q' })], '/work/p'), null)
})
