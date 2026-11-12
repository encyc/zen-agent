/** The workspace mode: coding is the shell's default, and every switch
 * persists so a relaunch lands back in the same surface. The zen branch's
 * legacy `zen.ui.mode` key migrates into `zen.workspaceMode` once. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useSettingsStore } from '../src/state/stores.ts'

async function withStorage(body: (store: Map<string, string>) => Promise<void> | void): Promise<void> {
  const store = new Map<string, string>()
  const fake = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  }
  const saved = (globalThis as { localStorage?: unknown }).localStorage
  ;(globalThis as { localStorage?: unknown }).localStorage = fake
  try {
    await body(store)
  } finally {
    ;(globalThis as { localStorage?: unknown }).localStorage = saved
  }
}

test('workspaceMode defaults to coding', () => {
  assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
})

test('setWorkspaceMode persists to zen.workspaceMode', async () => {
  await withStorage((store) => {
    useSettingsStore.getState().setWorkspaceMode('swarm')

    assert.equal(useSettingsStore.getState().workspaceMode, 'swarm')
    assert.equal(store.get('zen.workspaceMode'), JSON.stringify('swarm'))

    useSettingsStore.getState().setWorkspaceMode('coding')
    assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
    assert.equal(store.get('zen.workspaceMode'), JSON.stringify('coding'))
  })
})

test('a legacy zen.ui.mode="zen" migrates to zen.workspaceMode once', async () => {
  await withStorage(async (store) => {
    store.set('zen.ui.mode', JSON.stringify('zen'))
    const specifier = '../src/state/stores.ts?migrate=' + Date.now()
    const mod = await import(specifier)
    assert.equal(mod.useSettingsStore.getState().workspaceMode, 'zen')
    assert.equal(store.get('zen.workspaceMode'), JSON.stringify('zen'))
  })
})

test('a foreign persisted workspaceMode value falls back to coding', async () => {
  await withStorage(async (store) => {
    store.set('zen.workspaceMode', JSON.stringify('banana'))
    const specifier = '../src/state/stores.ts?foreign=' + Date.now()
    const mod = await import(specifier)
    assert.equal(mod.useSettingsStore.getState().workspaceMode, 'coding')
  })
})
