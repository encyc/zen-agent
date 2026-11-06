/** When to look for a release at startup. Kept pure so the decision can be
 * tested without the Tauri updater plugin; the store below is the thin glue
 * that talks to the plugin and to localStorage. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { bootUpdateCheck } from '../src/state/updateStore.ts'

const base = {
  current: '0.8.1',
  settings: { updateChecks: true },
  envDisabled: false,
  busy: false,
  pendingApprovals: 0,
}

test('the env switch and the settings switch both silence the boot check', () => {
  assert.equal(bootUpdateCheck({ ...base, envDisabled: true }), 'skip')
  assert.equal(bootUpdateCheck({ ...base, settings: { updateChecks: false } }), 'skip')
})

test('a fresh cache answers without touching the network', () => {
  assert.equal(
    bootUpdateCheck({ ...base, cache: { checkedAt: Date.now(), latest: '0.9.0', notified: null } }),
    'cache',
  )
})

test('a stale cache triggers one network check', () => {
  assert.equal(
    bootUpdateCheck({ ...base, cache: { checkedAt: 0, latest: '0.9.0', notified: null } }),
    'network',
  )
})

test('a missing cache triggers one network check', () => {
  assert.equal(bootUpdateCheck(base), 'network')
})
