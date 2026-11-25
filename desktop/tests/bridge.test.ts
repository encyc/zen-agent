import assert from 'node:assert/strict'
import { test } from 'node:test'
import { installBridge, initialState, type BridgeState } from '../src/dev/bridge.ts'
import { PROTOCOL_VERSION } from '../src/rpc/types.ts'

/** Headless bridge harness: install the scripted Tauri surface and drive it
 * through the same `rpc_send` invoke the real shell uses, with no Rust side.
 * T9 extends this to cover the default→error and limits paths. */
function bootBridge(overrides: Partial<BridgeState> = {}) {
  const state = initialState(overrides)
  ;(globalThis as any).window = {}
  installBridge(state)
  const internals = (globalThis as any).window.__TAURI_INTERNALS__
  return {
    state,
    invoke: (payload: Record<string, unknown>) =>
      internals.invoke('rpc_send', { payload }) as Promise<Record<string, unknown>>,
  }
}

test('bridge hello reports the pinned protocol version', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'hello', id: 'h1' })
  assert.equal(frame.protocol_version, PROTOCOL_VERSION)
  assert.equal(frame.ok, true)
})

test('unknown request kind returns an error frame, not fake success', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'no_such_kind', id: 'x' })
  assert.equal(frame.type, 'error')
  assert.equal(frame.ok, undefined)
})

test('limits get carries the registry tables with their choices', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'limits', op: 'get', id: 'l1' })
  const knobs = frame.knobs as Record<string, any>[]
  const byKey = new Map(knobs.map((knob) => [knob.key, knob]))

  assert.equal(byKey.get('limits.skills.max_index_bytes')?.default, 12288)
  assert.equal(byKey.get('task.timeout_s')?.table, 'task')
  assert.equal(byKey.get('task.timeout_s')?.value, 900)
})
