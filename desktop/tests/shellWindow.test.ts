/** The shell boots with `visible: false`, so one predicate decides when the
 * window may appear: the ready tree, or a failed boot whose ExitScreen the
 * person has to see. Everything else stays hidden. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  BOOT_REVEAL_FALLBACK_MS,
  createWindowRevealer,
  shouldShowWindow,
} from '../src/state/shellWindow.ts'

test('the window shows for a ready shell and for a failed boot', () => {
  assert.equal(shouldShowWindow('ready'), true)
  assert.equal(shouldShowWindow('exited'), true)
})

test('the window stays hidden while the shell is idle or starting', () => {
  assert.equal(shouldShowWindow('idle'), false)
  assert.equal(shouldShowWindow('starting'), false)
})

test('the ready effect and the boot timer share exactly one reveal', async () => {
  let shows = 0
  const reveal = createWindowRevealer(async () => {
    shows += 1
  })

  await Promise.all([reveal(), reveal()])
  await reveal()

  assert.equal(shows, 1)
})

test('a rejected show is retried instead of consuming the fallback', async () => {
  let shows = 0
  const reveal = createWindowRevealer(async () => {
    shows += 1
    if (shows === 1) throw new Error('window plugin not ready')
  })

  await assert.rejects(reveal(), /not ready/)
  await reveal()
  await reveal()

  assert.equal(shows, 2, 'the second caller retried after the rejection')
})

test('the unconditional boot fallback fires later than the first-paint timer', () => {
  // App reveals on rAF/500ms; the dead-end fallback must be a genuine later
  // guarantee, not a second race with the ready effect.
  assert.ok(BOOT_REVEAL_FALLBACK_MS >= 1500)
  assert.ok(BOOT_REVEAL_FALLBACK_MS <= 2000)
})
