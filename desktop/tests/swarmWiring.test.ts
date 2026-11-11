import test from 'node:test'
import assert from 'node:assert/strict'

import { applySwarmFrame } from '../src/state/swarmWiring.ts'
import { useSwarmStore } from '../src/state/swarmStore.ts'
import { useNotificationStore } from '../src/state/stores.ts'

/** Swarm frames are untagged and global: applySwarmFrame must consume them
 * before appStore's project/session filters would drop them. These tests pin
 * the exact consumption contract the handleFrame head delegates to. */

function reset(): void {
  useSwarmStore.setState({
    messages: [],
    tasks: [],
    agents: [],
    roles: [],
    warnings: [],
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
