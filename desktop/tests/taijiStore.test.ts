import test from 'node:test'
import assert from 'node:assert/strict'
import { useTaijiStore, _useClient } from '../src/state/taijiStore.ts'

type FakeReply = Record<string, unknown>

/** A fake RpcClient: records sent frames, answers from a script. */
function fakeClient(script: (payload: Record<string, unknown>) => FakeReply) {
  const sent: Record<string, unknown>[] = []
  return {
    sent,
    send(payload: Record<string, unknown>): Promise<FakeReply> {
      sent.push(payload)
      return Promise.resolve(script(payload))
    },
  }
}

function install(fake: ReturnType<typeof fakeClient>): void {
  _useClient(async () => fake)
}

test('refresh stores the board and clears failure', async () => {
  const board = {
    active: true,
    router_busy: false,
    slots: { busy: 1, max: 3, queued: 0 },
    workers: [{ name: 'w1', status: 'ok', prompt: 'p', diffstat: [] }],
  }
  const fake = fakeClient((payload) => {
    assert.equal(payload.type, 'taiji')
    assert.equal(payload.op, 'board')
    return { ok: true, board }
  })
  install(fake)
  useTaijiStore.setState({ board: null, failed: true })

  await useTaijiStore.getState().refresh()

  assert.deepEqual(useTaijiStore.getState().board, board)
  assert.equal(useTaijiStore.getState().failed, false)
})

test('refresh marks failure without losing the last board', async () => {
  const stale = { active: true, router_busy: false, slots: null, workers: [] }
  useTaijiStore.setState({ board: stale, failed: false })
  install(
    fakeClient(() => ({ ok: false, message: 'taiji 未在此宿主启用' })),
  )

  await useTaijiStore.getState().refresh()

  assert.equal(useTaijiStore.getState().failed, true)
  assert.deepEqual(useTaijiStore.getState().board, stale)
})

test('send routes a message op and reports the queued flag', async () => {
  const fake = fakeClient((payload) => {
    assert.equal(payload.op, 'message')
    assert.equal(payload.name, 'w1')
    assert.equal(payload.text, 'hello')
    return { ok: true, queued: true, status: 'running' }
  })
  install(fake)

  const reply = await useTaijiStore.getState().send('w1', 'hello')

  assert.equal(reply.ok, true)
  assert.equal(reply.queued, true)
  assert.equal(fake.sent.length, 1)
})

test('merge and kill route their ops', async () => {
  const fake = fakeClient((payload) => ({ ok: true }))
  install(fake)
  const store = useTaijiStore.getState()

  await store.merge('w1')
  await store.kill('w2')

  assert.deepEqual(
    fake.sent.map((frame) => frame.op),
    ['merge', 'kill'],
  )
})

test('peek returns the transcript tail items', async () => {
  const items = [{ role: 'user', text: 'hi' }]
  install(fakeClient((payload) => ({ ok: true, items })))
  const store = useTaijiStore.getState()

  const reply = await store.peek('w1', 12)

  assert.deepEqual(reply.items, items)
})
