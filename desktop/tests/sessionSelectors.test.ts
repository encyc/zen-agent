import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canForkSession, isBlockedSession } from '../src/state/sessionSelectors.ts'

test('fork is offered only for the current session', () => {
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/a.jsonl'), true)
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/b.jsonl'), false)
  assert.equal(canForkSession(null, '/sessions/a.jsonl'), false)
})

test('a recorded project root makes any session openable', () => {
  assert.equal(
    isBlockedSession({ path: '/sessions/c.jsonl', project_path: '/work/q' }, '/sessions/a.jsonl'),
    false,
  )
  assert.equal(
    isBlockedSession({ path: '/sessions/a.jsonl', project_path: '/work/p' }, '/sessions/a.jsonl'),
    false,
  )
  assert.equal(isBlockedSession({ path: '/sessions/a.jsonl' }, '/sessions/a.jsonl'), false)
})

test('rootless sessions stay discovery-only unless they are current', () => {
  assert.equal(isBlockedSession({ path: '/sessions/b.jsonl' }, '/sessions/a.jsonl'), true)
  assert.equal(isBlockedSession({ path: '/sessions/b.jsonl', project_path: '' }, null), true)
  assert.equal(isBlockedSession({ path: '/sessions/b.jsonl' }, null), true)
})
