import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  applySwarmFrame,
  registerSwarmSender,
  syncSwarmState,
} from '../src/state/swarmWiring.ts'
import { _useSwarmSender } from '../src/state/swarmStore.ts'
import { useSwarmStore } from '../src/state/swarmStore.ts'
import { useNotificationStore, useSettingsStore } from '../src/state/stores.ts'

/** Swarm frames carry the project they belong to: only the active room's
 * frames may land in the store, and only its @you notices become
 * notifications. These tests pin the exact consumption contract the
 * handleFrame head delegates to. */

function reset(): void {
  useSwarmStore.setState({
    messages: [],
    tasks: [],
    agents: [],
    roles: [],
    warnings: [],
    runRows: {},
    loaded: false,
  })
  useNotificationStore.setState({ items: [], unread: 0 })
}

const messageFrame = {
  type: 'swarm_message',
  swarm: true,
  id: 'm1',
  ts: 0,
  author: { kind: 'user', id: 'you' },
  text: 'hello @research',
  mentions: ['research'],
  taskId: null,
  chainDepth: 0,
} as const

test('applySwarmFrame appends room messages and reports them consumed', () => {
  reset()
  const consumed = applySwarmFrame(messageFrame)
  assert.equal(consumed, true)
  const messages = useSwarmStore.getState().messages
  assert.equal(messages.length, 1)
  assert.equal(messages[0].id, 'm1')
})

test('applySwarmFrame surfaces @you notices as info notifications, not room rows', () => {
  reset()
  const consumed = applySwarmFrame({
    type: 'swarm_notice',
    agent: 'research',
    text: 'you were mentioned',
  })
  assert.equal(consumed, true)
  assert.deepEqual(
    useNotificationStore.getState().items.map((item) => item.tone),
    ['info'],
  )
  assert.equal(useSwarmStore.getState().messages.length, 0)
})

test('applySwarmFrame leaves empty notices and non-swarm frames unconsumed', () => {
  reset()
  // An empty notice is room noise: no notification, no swarm tag, fall through.
  assert.equal(applySwarmFrame({ type: 'swarm_notice', agent: 'research', text: '' }), false)
  // A tagged zen frame belongs to the normal project/session path.
  assert.equal(applySwarmFrame({ type: 'zen_fleet', enabled: true, roster: [] }), false)
  assert.equal(useNotificationStore.getState().items.length, 0)
  assert.equal(useSwarmStore.getState().messages.length, 0)
})

test('registerSwarmSender wires the app client into the swarm store', async () => {
  reset()
  const sent: Array<Record<string, unknown>> = []
  registerSwarmSender({
    send: async (request) => {
      sent.push(request)
      return { type: 'result', ok: true }
    },
  })
  await useSwarmStore.getState().post('hello room')
  assert.equal(sent.length, 1)
  assert.equal(sent[0].type, 'swarm')
  assert.equal(sent[0].op, 'post')
  // Restore the module-level sender seam for the other tests.
  _useSwarmSender({
    send: async () => {
      throw new Error('no client')
    },
  })
})

test('applySwarmFrame consumes swarm_event frames into live run rows', () => {
  reset()
  useSwarmStore.setState({ runRows: {} })
  const consumed = applySwarmFrame({
    type: 'swarm_event',
    swarm: true,
    agent: 'research',
    task: 't_1',
    event: { type: 'tool_execution_start', tool_call_id: 'c1', tool_name: 'bash' },
  })
  assert.equal(consumed, true)
  assert.equal(useSwarmStore.getState().runRows['t_1'].length, 1)
})

test('every swarm_* frame type in the union has a handler', () => {
  // The swarm frame interfaces live in types-swarm.ts (re-exported by types.ts),
  // so the union spans both files. applySwarmFrame consumes *any* swarm:true
  // frame: a new swarm_* type added without a handler is silently swallowed.
  const types = ['../src/rpc/types.ts', '../src/rpc/types-swarm.ts']
    .map((p) => readFileSync(new URL(p, import.meta.url), 'utf8')).join('\n')
  const swarmTypes = [...types.matchAll(/type:\s*'(swarm_[a-z_]+)'/g)].map((m) => m[1])
  assert.ok(swarmTypes.length >= 5, 'union lost swarm_* members')
  const handled = ['../src/state/swarmStore.ts', '../src/state/swarmWiring.ts']
    .map((p) => readFileSync(new URL(p, import.meta.url), 'utf8')).join('\n')
  for (const t of swarmTypes) {
    assert.ok(handled.includes(`'${t}'`), `${t} has no handler — would be silently swallowed`)
  }
})

test('a tagged swarm frame for a background project is not applied', () => {
  reset()
  const applied = applySwarmFrame({ ...messageFrame, project: '/p/b' }, '/p/a')
  assert.equal(applied, false)
  assert.equal(useSwarmStore.getState().messages.length, 0)
})

test('a tagged swarm frame for the active project is applied', () => {
  reset()
  const applied = applySwarmFrame({ ...messageFrame, project: '/p/a' }, '/p/a')
  assert.equal(applied, true)
  assert.equal(useSwarmStore.getState().messages.length, 1)
})

test('a notice from a background project does not surface', () => {
  reset()
  const applied = applySwarmFrame(
    {
      type: 'swarm_notice',
      swarm: true,
      project: '/p/b',
      agent: 'research',
      text: 'you were mentioned',
    },
    '/p/a',
  )
  assert.equal(applied, false)
  assert.equal(useNotificationStore.getState().items.length, 0)
})

test('syncSwarmState resets stale rows and reloads the active room', async () => {
  reset()
  useSettingsStore.setState({ workspaceMode: 'swarm' })
  // runRows come from live events only, so `load` alone cannot clear them:
  // they prove the reset ran before the snapshot landed.
  useSwarmStore.setState({
    messages: [messageFrame],
    runRows: { t_old: [{ kind: 'assistant', id: 'v1', text: 'stale' } as never] },
    loaded: true,
  })
  const sent: Array<Record<string, unknown>> = []
  _useSwarmSender({
    send: async (request) => {
      sent.push(request)
      return {
        type: 'result',
        ok: true,
        roles: [],
        agents: [],
        tasks: [],
        messages: [
          {
            id: 'm2',
            ts: 1,
            author: { kind: 'system', id: 'system' },
            text: 'other room',
            mentions: [],
            taskId: null,
            chainDepth: 0,
          },
        ],
        warnings: [],
      }
    },
  })
  await syncSwarmState('/p/a')
  assert.deepEqual(sent, [{ type: 'swarm', op: 'list', project: '/p/a' }])
  const state = useSwarmStore.getState()
  assert.deepEqual(state.messages.map((message) => message.id), ['m2'])
  assert.deepEqual(state.runRows, {})
  assert.equal(state.loaded, true)
  assert.equal(state.loadedProject, '/p/a')
})

test('syncSwarmState is a no-op outside swarm mode', async () => {
  reset()
  useSettingsStore.setState({ workspaceMode: 'coding' })
  let calls = 0
  _useSwarmSender({
    send: async () => {
      calls += 1
      return { type: 'result', ok: true }
    },
  })
  await syncSwarmState()
  assert.equal(calls, 0)
})
