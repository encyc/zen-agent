import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canForkSession } from '../src/state/sessionSelectors.ts'

test('fork is offered only for the current session', () => {
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/a.jsonl'), true)
  assert.equal(canForkSession('/sessions/a.jsonl', '/sessions/b.jsonl'), false)
  assert.equal(canForkSession(null, '/sessions/a.jsonl'), false)
})
