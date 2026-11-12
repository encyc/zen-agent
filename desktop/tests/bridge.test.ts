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

test('unknown request kind returns an error frame, not fake success', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'no_such_kind', id: 'x' })
  assert.equal(frame.type, 'error')
  assert.equal(frame.ok, undefined)
})

test('zen_fleet enable returns a router roster', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'zen_fleet', op: 'enable', id: 'z' })
  assert.equal(frame.ok, true)
  assert.equal(frame.enabled, true)
  assert.ok(Array.isArray(frame.roster) && frame.roster.some((r: any) => r.role === 'router'))
})

test('zen_fleet state reflects a prior enable', async () => {
  const { invoke } = bootBridge()
  await invoke({ type: 'zen_fleet', op: 'enable', id: 'z' })
  const frame = await invoke({ type: 'zen_fleet', op: 'state', id: 's' })
  assert.equal(frame.enabled, true)
})

test('zen_fleet disable clears the roster', async () => {
  const { invoke } = bootBridge()
  await invoke({ type: 'zen_fleet', op: 'enable', id: 'z' })
  const frame = await invoke({ type: 'zen_fleet', op: 'disable', id: 'd' })
  assert.equal(frame.ok, true)
  assert.equal(frame.enabled, false)
  assert.deepEqual(frame.roster, [])
})

/** Collect the frames the mock pushes through the Tauri event sink. */
function captureFrames() {
  const frames: Record<string, unknown>[] = []
  ;(globalThis as any).window.__ZEN_E2E_EMIT__ = (_event: string, payload: any) => {
    frames.push(payload.payload.frame)
  }
  return frames
}

async function waitForFrame(
  frames: Record<string, unknown>[],
  pred: (frame: Record<string, unknown>) => boolean,
  timeoutMs = 3000,
): Promise<Record<string, unknown> | undefined> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const found = frames.find(pred)
    if (found !== undefined) return found
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  return undefined
}

test('an agent message mentioning you emits a swarm_notice with swarm:true', async () => {
  const { invoke } = bootBridge()
  const frames = captureFrames()
  await invoke({ type: 'swarm', op: 'post', text: '@builder 复核一下', id: 'p' })
  const notice = await waitForFrame(frames, (f) => f.type === 'swarm_notice')
  assert.ok(notice, 'a swarm_notice frame was emitted')
  assert.equal(notice.swarm, true)
  assert.equal(notice.agent, 'builder')
  assert.ok(String(notice.text).includes('@builder'))
})
test('limits get carries the zen and swarm tables with their choices', async () => {
  const { invoke } = bootBridge()
  const frame = await invoke({ type: 'limits', op: 'get', id: 'l1' })
  const knobs = frame.knobs as Record<string, any>[]
  const byKey = new Map(knobs.map((knob) => [knob.key, knob]))

  assert.equal(byKey.get('zen.max_concurrent')?.default, 3)
  assert.equal(byKey.get('task.timeout_s')?.table, 'task')
  const mode = byKey.get('zen.worker_mode')
  assert.equal(mode?.kind, 'str')
  assert.equal(mode?.value, 'inherit')
  assert.deepEqual(mode?.choices, ['inherit', 'ask', 'plan', 'zen'])
  assert.equal(byKey.get('swarm.max_chain_cost')?.default, 5)
})
