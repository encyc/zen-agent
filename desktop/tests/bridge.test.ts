import assert from 'node:assert/strict'
import { test } from 'node:test'
import { installBridge, initialState, type BridgeState } from '../src/dev/bridge.ts'
import { PROTOCOL_VERSION } from '../src/rpc/types.ts'

/** Headless bridge harness: install the scripted Tauri surface and drive it
 * through the same `rpc_send` invoke the real shell uses, with no Rust side.
 * T9 extends this to cover the default→error, zen_fleet and notice paths. */
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