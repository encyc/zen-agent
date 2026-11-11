import test from 'node:test'
import assert from 'node:assert/strict'

import { useZenStore } from '../src/state/zenStore.ts'
import { useSettingsStore } from '../src/state/stores.ts'
import { REQUEST_KINDS, PROTOCOL_VERSION } from '../src/rpc/types.ts'
import type { RosterItem } from '../src/rpc/types.ts'

function row(overrides: Partial<RosterItem> = {}): RosterItem {
  return {
    path: '/p/sessions/w1.jsonl',
    title: 'w1',
    focus: '编译修复',
    role: 'worker',
    status: 'waiting',
    last_report: 'done',
    ...overrides,
  }
}

function resetStores(): void {
  useZenStore.getState().reset()
  useZenStore.getState().closeWorker()
  useSettingsStore.getState().setWorkspaceMode('coding')
}

test('REQUEST_KINDS carries zen_fleet and swarm at protocol version 3', () => {
  const kinds = REQUEST_KINDS as readonly string[]
  assert.ok(kinds.includes('zen_fleet'))
  assert.ok(kinds.includes('swarm'))
  assert.equal(PROTOCOL_VERSION, 3)
})

test('the workspace mode flips through the settings store', () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  assert.equal(useSettingsStore.getState().workspaceMode, 'zen')
  useSettingsStore.getState().setWorkspaceMode('swarm')
  assert.equal(useSettingsStore.getState().workspaceMode, 'swarm')
  useSettingsStore.getState().setWorkspaceMode('coding')
  assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
})

test('applyRoster stores enabled flag and rows', () => {
  resetStores()
  useZenStore.getState().applyRoster(true, [row({ role: 'router' }), row()])
  const state = useZenStore.getState()
  assert.equal(state.enabled, true)
  assert.equal(state.roster.length, 2)
  assert.equal(state.roster[0].role, 'router')
})

test('applyRoster(false, []) clears the fleet view', () => {
  resetStores()
  useZenStore.getState().applyRoster(true, [row()])
  useZenStore.getState().applyRoster(false, [])
  const state = useZenStore.getState()
  assert.equal(state.enabled, false)
  assert.deepEqual(state.roster, [])
})

test('openWorker/closeWorker track the drawer target', () => {
  resetStores()
  useZenStore.getState().openWorker('/p/sessions/w2.jsonl')
  assert.equal(useZenStore.getState().workerPath, '/p/sessions/w2.jsonl')
  useZenStore.getState().openWorker('/p/sessions/w3.jsonl')
  assert.equal(useZenStore.getState().workerPath, '/p/sessions/w3.jsonl')
  useZenStore.getState().closeWorker()
  assert.equal(useZenStore.getState().workerPath, null)
})

test('reset clears enabled and roster but keeps nothing stale', () => {
  useZenStore.getState().applyRoster(true, [row()])
  useZenStore.getState().reset()
  assert.equal(useZenStore.getState().enabled, false)
  assert.deepEqual(useZenStore.getState().roster, [])
})
