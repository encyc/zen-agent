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

test('install() re-checks when the pending handle was lost to a reload', async () => {
  ;(globalThis as Record<string, unknown>).__APP_VERSION__ ??= '0.8.4'
  const { useUpdateStore, _usePluginLoader } = await import('../src/state/updateStore.ts')
  let downloads = 0
  _usePluginLoader(async () => ({
    check: async () => ({
      version: '0.9.0',
      downloadAndInstall: async () => {
        downloads += 1
      },
    }),
  }))
  useUpdateStore.setState({
    cache: { checkedAt: Date.now(), latest: '0.9.0', notified: null },
    phase: 'available',
    pending: null,
    selfUpdatable: true,
    message: null,
  })
  await useUpdateStore.getState().install()
  assert.equal(downloads, 1)
})
