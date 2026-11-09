/** The shell boots with `visible: false`, so one predicate decides when the
 * window may appear: the ready tree, or a failed boot whose ExitScreen the
 * person has to see. Everything else stays hidden. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { shouldShowWindow } from '../src/state/shellWindow.ts'

test('the window shows for a ready shell and for a failed boot', () => {
  assert.equal(shouldShowWindow('ready'), true)
  assert.equal(shouldShowWindow('exited'), true)
})

test('the window stays hidden while the shell is idle or starting', () => {
  assert.equal(shouldShowWindow('idle'), false)
  assert.equal(shouldShowWindow('starting'), false)
})
