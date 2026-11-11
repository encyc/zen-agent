import test from 'node:test'
import assert from 'node:assert/strict'

import { syncZenState, setZenMode, zenTranscript, applyFleetFrame } from '../src/state/zenActions.ts'
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
  useSettingsStore.getState().setZenMode('coding')
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

test('syncZenState auto-enables when the persisted toggle is zen and the fleet is off', async () => {
  resetStores()
  useSettingsStore.getState().setZenMode('zen')
  const env = makeEnv((payload) =>
    payload.op === 'state' ? okState(false) : { type: 'result', ok: true, session: '/p/r.jsonl', roster: [] },
  )
  await syncZenState(env.get)
  assert.ok(env.sent.some((payload) => payload.op === 'enable'), 'enable was sent')
  assert.equal(useZenStore.getState().enabled, true)
})

test('syncZenState never auto-disables: toggle coding leaves the fleet alone', async () => {
  resetStores()
  const env = makeEnv(() => okState(true))
  await syncZenState(env.get)
  assert.ok(!env.sent.some((payload) => payload.op === 'disable'), 'no disable sent')
  assert.equal(useZenStore.getState().enabled, true)
})

test('syncZenState with the fleet already on just applies the roster', async () => {
  resetStores()
  useSettingsStore.getState().setZenMode('zen')
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

test("setZenMode zen enables, follows the session and refreshes", async () => {
  resetStores()
  const env = makeEnv(() => ({
    type: 'result',
    ok: true,
    session: '/p/router.jsonl',
    roster: [{ path: '/p/router.jsonl', title: 'r', focus: '', role: 'router', status: 'idle', last_report: '' }],
  }))
  await setZenMode(env.get, 'zen')
  assert.equal(useSettingsStore.getState().zenMode, 'zen')
  assert.equal(useZenStore.getState().enabled, true)
  assert.equal(useSessionStore.getState().currentPath, '/p/router.jsonl')
  assert.ok(env.refreshed.includes('transcript') && env.refreshed.includes('sessions'))
})

test('setZenMode reverts the toggle when the backend refuses (busy slot)', async () => {
  resetStores()
  const env = makeEnv(() => ({ type: 'error', code: 'zen_busy', message: 'zen needs an idle session' }))
  await setZenMode(env.get, 'zen')
  assert.equal(useSettingsStore.getState().zenMode, 'coding')
  assert.equal(useZenStore.getState().enabled, false)
})

test("setZenMode coding disables and clears the fleet view", async () => {
  resetStores()
  useSettingsStore.getState().setZenMode('zen')
  useZenStore.getState().applyRoster(true, [])
  const env = makeEnv(() => ({ type: 'result', ok: true, session: '/p/old.jsonl', roster: [] }))
  await setZenMode(env.get, 'coding')
  assert.equal(useSettingsStore.getState().zenMode, 'coding')
  assert.equal(useZenStore.getState().enabled, false)
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
