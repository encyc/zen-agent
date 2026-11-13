import test from 'node:test'
import assert from 'node:assert/strict'

import {
  applyFleetFrame,
  onZenModeLeft,
  stopFleet,
  switchWorkspaceMode,
  switchZenPresence,
  syncWorkspaceState,
  syncZenState,
  zenTranscript,
} from '../src/state/zenActions.ts'
import { useSettingsStore } from '../src/state/stores.ts'
import { useSessionStore } from '../src/state/stores.ts'
import { useNoticeStore } from '../src/state/stores.ts'
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
  useSessionStore.setState({ currentPath: null })
  useNoticeStore.setState({ notice: null })
}

const busyRefusal = { type: 'error', code: 'zen_busy', message: 'zen needs an idle session' }

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
  const env = makeEnv(() => ({ ...busyRefusal }))
  await switchZenPresence(env.get, true)
  assert.equal(useSettingsStore.getState().workspaceMode, 'coding')
  assert.equal(useZenStore.getState().enabled, false)
})

test('a refused stop surfaces on the notice banner instead of failing silently', async () => {
  resetStores()
  useZenStore.getState().applyRoster(true, [])
  const env = makeEnv(() => ({ ...busyRefusal }))
  await stopFleet(env.get)
  // The fleet is untouched and the user sees why.
  assert.equal(useZenStore.getState().enabled, true)
  assert.equal(useNoticeStore.getState().notice?.kind, 'error')
})

test('switchZenPresence leaving stops the fleet and restores the pre-zen session', async () => {
  resetStores()
  const env = makeEnv(() => ({
    type: 'result',
    ok: true,
    session: '/p/old.jsonl',
    roster: [],
  }))
  await switchZenPresence(env.get, false)
  assert.ok(env.sent.some((payload) => payload.op === 'disable'), 'disable was sent')
  assert.equal(useZenStore.getState().enabled, false)
  assert.equal(useSessionStore.getState().currentPath, '/p/old.jsonl')
})

test('onZenModeLeft stops the fleet: coding never inherits a live router', async () => {
  resetStores()
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

test('onZenModeLeft keeps the zen surface when the backend refuses the stop', async () => {
  resetStores()
  useZenStore.getState().applyRoster(true, [])
  const env = makeEnv(() => ({ ...busyRefusal }))
  await onZenModeLeft(env.get)
  assert.equal(useSettingsStore.getState().workspaceMode, 'zen')
  assert.equal(useZenStore.getState().enabled, true)
  assert.equal(useNoticeStore.getState().notice?.kind, 'error')
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

test('syncZenState does not auto-enable after the user stopped the fleet', async () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  useZenStore.getState().markStopped()
  const env = makeEnv((p) => (p.op === 'state' ? okState(false) : { type: 'result', ok: true }))
  await syncZenState(env.get)
  assert.ok(!env.sent.some((p) => p.op === 'enable'), 'no revival while stopped')
  assert.equal(useZenStore.getState().enabled, false)
})

test('explicit zen entry clears the stopped flag and enables', async () => {
  resetStores()
  useZenStore.getState().markStopped()
  const env = makeEnv(() => ({ type: 'result', ok: true, session: '/p/r.jsonl', roster: [] }))
  await switchZenPresence(env.get, true)
  assert.equal(useZenStore.getState().stoppedByUser, false)
  assert.equal(useZenStore.getState().enabled, true)
})

test('stopFleet marks the fleet stopped', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'result', ok: true, roster: [] }))
  await stopFleet(env.get)
  assert.equal(useZenStore.getState().stoppedByUser, true)
})


test('syncWorkspaceState adopts the backend mode', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'result', ok: true, mode: 'swarm' }))
  await syncWorkspaceState(env.get)
  assert.equal(useSettingsStore.getState().workspaceMode, 'swarm')
})

test('syncWorkspaceState ignores an unknown or failed mode', async () => {
  resetStores()
  useSettingsStore.getState().setWorkspaceMode('zen')
  const env = makeEnv(() => ({ type: 'result', ok: true, mode: 'banana' }))
  await syncWorkspaceState(env.get)
  assert.equal(useSettingsStore.getState().workspaceMode, 'zen')
})

test('switchWorkspaceMode writes the backend mode', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'result', ok: true, mode: 'coding' }))
  switchWorkspaceMode(env.get, 'coding')
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.ok(
    env.sent.some(
      (payload) =>
        payload.type === 'workspace' &&
        payload.op === 'set' &&
        payload.mode === 'coding',
    ),
    'workspace set was sent',
  )
})
