import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canForkSession, isCrossProject } from '../src/state/sessionSelectors.ts'

test('fork is offered only for the current session', () => {
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/a.jsonl'), true)
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/b.jsonl'), false)
  assert.equal(canForkSession(null, '/sessions/a.jsonl'), false)
})

test('cross-project detection fails closed when the current session is unknown', () => {
  assert.equal(isCrossProject('/sessions/a.jsonl', 'p', '/sessions/a.jsonl', null), false)
  assert.equal(isCrossProject('/sessions/b.jsonl', 'p', '/sessions/a.jsonl', 'p'), false)
  assert.equal(isCrossProject('/sessions/c.jsonl', 'q', '/sessions/a.jsonl', 'p'), true)
  assert.equal(isCrossProject('/sessions/b.jsonl', 'p', null, null), true)
  assert.equal(isCrossProject('/sessions/b.jsonl', 'p', '/sessions/a.jsonl', null), true)
})
