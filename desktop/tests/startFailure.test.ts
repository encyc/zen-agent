/** A failed `rpc_start` is the only way the shell learns its runtime is gone;
 * these helpers decide whether that is a missing binary or something else. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isRuntimeMissing, probeHint, startFailureOf } from '../src/state/startFailure.ts'

test('a structured start error keeps its code, bin and os error', () => {
  const failure = startFailureOf({
    code: 'runtime_missing',
    bin: '/app/zen',
    osError: 'No such file or directory (os error 2)',
  })
  assert.deepEqual(failure, {
    code: 'runtime_missing',
    bin: '/app/zen',
    osError: 'No such file or directory (os error 2)',
  })
})

test('a plain string rejection is not treated as a structured failure', () => {
  assert.equal(startFailureOf('failed to start zen rpc: No such file or directory'), null)
})

test('only the runtime_missing code renders the runtime card', () => {
  assert.equal(isRuntimeMissing({ code: 'runtime_missing', bin: '/app/zen', osError: '' }), true)
  assert.equal(isRuntimeMissing({ code: 'other', bin: '/app/zen', osError: '' }), false)
  assert.equal(isRuntimeMissing(null), false)
})

test('the probe hint names the resolved path only when it exists', () => {
  assert.equal(probeHint({ resolved: '/app/zen', exists: true }), '/app/zen')
  assert.equal(probeHint({ resolved: null, exists: false }), '')
})
