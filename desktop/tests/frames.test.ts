import assert from 'node:assert/strict'
import { test } from 'node:test'
import { frameError } from '../src/rpc/frames.ts'

test('error frames and rejected results both yield a message', () => {
  assert.equal(frameError({ type: 'error', message: 'boom' }), 'boom')
  assert.equal(frameError({ type: 'result', ok: false, reason: 'nope' }), 'nope')
  assert.equal(frameError({ type: 'result', ok: false }), 'request failed')
  assert.equal(frameError({ type: 'result', ok: true }), null)
})
