/** The workspace mode: coding is the shell's default, and every switch
 * persists so a relaunch lands back in the same surface. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useSettingsStore } from '../src/state/stores.ts'

function withStorage(body: (store: Map<string, string>) => void): void {
  const store = new Map<string, string>()
  const fake = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  }
  const saved = (globalThis as { localStorage?: unknown }).localStorage
  ;(globalThis as { localStorage?: unknown }).localStorage = fake
  try {
    body(store)
  } finally {
    ;(globalThis as { localStorage?: unknown }).localStorage = saved
  }
}

test('workspaceMode defaults to coding', () => {
  assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
})

test('setWorkspaceMode persists to zen.workspaceMode', () => {
  withStorage((store) => {
    useSettingsStore.getState().setWorkspaceMode('swarm')

    assert.equal(useSettingsStore.getState().workspaceMode, 'swarm')
    assert.equal(store.get('zen.workspaceMode'), JSON.stringify('swarm'))

    useSettingsStore.getState().setWorkspaceMode('coding')
    assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
    assert.equal(store.get('zen.workspaceMode'), JSON.stringify('coding'))
  })
})
