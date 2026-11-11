import test from 'node:test'
import assert from 'node:assert/strict'

import {
  applyFleetFrame,
  onZenModeLeft,
  stopFleet,
  switchZenPresence,
  syncZenState,
  zenTranscript,
} from '../src/state/zenActions.ts'
import { useSettingsStore } from '../src/state/stores.ts'
import { useSessionStore } from '../src/state/stores.ts'
import { useZenStore } from '../src/state/zenStore.ts'

/** A fake app-store getter: captures every wire payload, replies via
 * `respond` so each test scripts its own server behavior. */
function makeEnv(respond: (payload: Record<string, unknown>) => unknown) {
  const sent: Array<Record<string, unknown>> = []
  const refreshed: string[] = []
  const get = () => ({
    client: () => ({
      send: async (payload: Record<string, unknown>) => {
        sent.push(payload)
        return respond(payload)
      },
    }),
    refreshTranscript: async () => {
      refreshed.push('transcript')
      return true
    },
    refreshSessions: async () => {
      refreshed.push('sessions')
    },
  })
  return { sent, refreshed, get }
}

function resetStores(): void {
  useZenStore.getState().reset()
  useZenStore.getState().closeWorker()
  useSettingsStore.getState().setWorkspaceMode('coding')
  useSettingsStore.getState().setZenBackgroundRun(true)
  useSessionStore.setState({ currentPath: null })
}

const okState = (enabled: boolean) => ({
  type: 'result',
  ok: true,
  enabled,
  roster: enabled
    ? [{ path: '/p/r.jsonl', title: 'r', focus: '', role: 'router', status: 'idle', last_report: '' }]
    : [],
})

test('syncZenState auto-enables when workspaceMode is zen and the fleet is off', async () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  const env = makeEnv((payload) =>
    payload.op === 'state' ? okState(false) : { type: 'result', ok: true, session: '/p/r.jsonl', roster: [] },
  )
  await syncZenState(env.get)
  assert.ok(env.sent.some((payload) => payload.op === 'enable'), 'enable was sent')
  assert.equal(useZenStore.getState().enabled, true)
})

test('syncZenState never auto-disables: leaving zen mode alone leaves the fleet alone', async () => {
  resetStores()
  const env = makeEnv(() => okState(true))
  await syncZenState(env.get)
  assert.ok(!env.sent.some((payload) => payload.op === 'disable'), 'no disable sent')
  assert.equal(useZenStore.getState().enabled, true)
})

test('syncZenState with the fleet already on just applies the roster', async () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  const env = makeEnv(() => okState(true))
  await syncZenState(env.get)
  assert.ok(!env.sent.some((payload) => payload.op === 'enable'), 'no re-enable sent')
  assert.equal(useZenStore.getState().roster.length, 1)
})

test('syncZenState survives a pre-zen child that errors on unknown kinds', async () => {
  resetStores()
  const env = makeEnv(() => {
    throw new Error('unknown request kind')
  })
  await syncZenState(env.get)
  assert.equal(useZenStore.getState().enabled, false)
})

test("switchZenPresence entering enables, follows the session and refreshes", async () => {
  resetStores()
  const env = makeEnv(() => ({
    type: 'result',
    ok: true,
    session: '/p/router.jsonl',
    roster: [{ path: '/p/router.jsonl', title: 'r', focus: '', role: 'router', status: 'idle', last_report: '' }],
  }))
  await switchZenPresence(env.get, true)
  assert.equal(useZenStore.getState().enabled, true)
  assert.equal(useSessionStore.getState().currentPath, '/p/router.jsonl')
  assert.ok(env.refreshed.includes('transcript') && env.refreshed.includes('sessions'))
})

test('switchZenPresence entering reverts workspaceMode when the backend refuses (busy slot)', async () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  const env = makeEnv(() => ({ type: 'error', code: 'zen_busy', message: 'zen needs an idle session' }))
  await switchZenPresence(env.get, true)
  assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
  assert.equal(useZenStore.getState().enabled, false)
})

test('switchZenPresence leaving sends nothing: the fleet keeps running in the background', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'result', ok: true, roster: [] }))
  await switchZenPresence(env.get, false)
  assert.equal(env.sent.length, 0)
})

test('onZenModeLeft stops the fleet when background running is off', async () => {
  resetStores()
  useSettingsStore.getState().setZenBackgroundRun(false)
  const env = makeEnv(() => ({
    type: 'result',
    ok: true,
    session: '/p/old.jsonl',
    roster: [],
  }))
  await onZenModeLeft(env.get)
  assert.ok(env.sent.some((payload) => payload.op === 'disable'), 'disable was sent')
  assert.equal(useZenStore.getState().enabled, false)
  assert.equal(useSessionStore.getState().currentPath, '/p/old.jsonl')
})

test('onZenModeLeft sends nothing while background running is on', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'result', ok: true, roster: [] }))
  await onZenModeLeft(env.get)
  assert.equal(env.sent.length, 0)
  assert.equal(useZenStore.getState().enabled, false)
})

test('stopFleet sends disable, clears the fleet and follows the restored session', async () => {
  resetStores()
  useZenStore.getState().applyRoster(true, [])
  const env = makeEnv(() => ({
    type: 'result',
    ok: true,
    session: '/p/old.jsonl',
    roster: [],
  }))
  await stopFleet(env.get)
  assert.equal(env.sent[0].op, 'disable')
  assert.equal(useZenStore.getState().enabled, false)
  assert.equal(useSessionStore.getState().currentPath, '/p/old.jsonl')
  assert.ok(env.refreshed.includes('transcript') && env.refreshed.includes('sessions'))
})

test('applyFleetFrame applies zen roster frames and reports them consumed', () => {
  resetStores()
  const consumed = applyFleetFrame({
    type: 'zen_fleet',
    enabled: true,
    roster: [
      { path: '/p/w1.jsonl', title: 'w1', focus: 'research', role: 'worker', status: 'working', last_report: '' },
    ],
  })
  assert.equal(consumed, true)
  assert.equal(useZenStore.getState().enabled, true)
  assert.equal(useZenStore.getState().roster.length, 1)
  assert.equal(useZenStore.getState().roster[0].focus, 'research')
})

test('applyFleetFrame leaves non-fleet frames for the normal path', () => {
  resetStores()
  assert.equal(applyFleetFrame({ type: 'swarm_notice', agent: 'research', text: 'hi' }), false)
  assert.equal(useZenStore.getState().enabled, false)
  assert.equal(useZenStore.getState().roster.length, 0)
})

test('zenTranscript returns the ok frame or null', async () => {
  resetStores()
  const okEnv = makeEnv(() => ({ type: 'result', ok: true, transcript: [], total: 0 }))
  const frame = await zenTranscript(okEnv.get, '/p/w1.jsonl')
  assert.equal(frame?.ok, true)
  assert.equal(okEnv.sent[0].path, '/p/w1.jsonl')

  const errEnv = makeEnv(() => ({ type: 'error', code: 'zen_invalid', message: 'no' }))
  assert.equal(await zenTranscript(errEnv.get, '/p/w1.jsonl'), null)
})
