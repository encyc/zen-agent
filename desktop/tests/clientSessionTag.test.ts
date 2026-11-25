/** The session auto-tag: requests whose kind addresses one live session carry
 * the current session so the host routes them to its resident slot — unless
 * the caller already named one, or the kind's `session` field means something
 * else (a terminal shell token, a runs filter). Ids are salted per process so
 * two windows sharing one pending map never collide. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { RpcClient, _useBridge, type Bridge } from '../src/rpc/client.ts'

interface Recorder {
  bridge: Bridge
  invokes: { command: string; args?: Record<string, unknown> }[]
}

function record(): Recorder {
  const invokes: Recorder['invokes'] = []
  return {
    invokes,
    bridge: {
      async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
        invokes.push({ command, args })
        return { type: 'result', id: 'x', ok: true } as T
      },
      async listen<T>(): Promise<() => void> {
        return () => {}
      },
      async relaunch(): Promise<void> {},
    },
  }
}

test('prompt carries the provider session so the host routes it to that slot', async () => {
  const recorder = record()
  _useBridge(recorder.bridge)
  const client = new RpcClient()
  client.setProject('/work/p')
  client.setSessionProvider(() => '/work/p/sessions/b.jsonl')

  await client.send({ type: 'prompt', text: 'hi' })

  const payload = recorder.invokes[0]?.args?.payload as Record<string, unknown>
  assert.equal(payload.session, '/work/p/sessions/b.jsonl')
  assert.equal(payload.project, '/work/p')
})

test('a caller-set session field always wins over the provider', async () => {
  const recorder = record()
  _useBridge(recorder.bridge)
  const client = new RpcClient()
  client.setProject('/work/p')
  client.setSessionProvider(() => '/work/p/sessions/a.jsonl')

  await client.send({
    type: 'transcript',
    session: '/work/p/sessions/c.jsonl',
    limit: 50,
  })

  const payload = recorder.invokes[0]?.args?.payload as Record<string, unknown>
  assert.equal(payload.session, '/work/p/sessions/c.jsonl')
})

test('kinds whose session field means something else are never tagged', async () => {
  const recorder = record()
  _useBridge(recorder.bridge)
  const client = new RpcClient()
  client.setProject('/work/p')
  client.setSessionProvider(() => '/work/p/sessions/a.jsonl')

  await client.send({ type: 'terminal', op: 'write', session: 'tok123', data: 'ls' })
  await client.send({ type: 'hello' })
  await client.send({ type: 'runs', op: 'list' })
  await client.send({ type: 'project', op: 'open', root: '/x' })

  const sessions = recorder.invokes.map(
    (call) => (call.args?.payload as Record<string, unknown> | undefined)?.session,
  )
  // The terminal token survives untouched; the host-level kinds stay clean.
  assert.deepEqual(sessions, ['tok123', undefined, undefined, undefined])
})

test('a null provider (no session on screen) sends nothing extra', async () => {
  const recorder = record()
  _useBridge(recorder.bridge)
  const client = new RpcClient()
  client.setProject('/work/p')
  client.setSessionProvider(() => null)

  await client.send({ type: 'prompt', text: 'hi' })

  const payload = recorder.invokes[0]?.args?.payload as Record<string, unknown>
  assert.equal('session' in payload, false)
})

test('ids are salted per process: two clients never collide, none contain a colon', async () => {
  const recorder = record()
  _useBridge(recorder.bridge)
  const one = new RpcClient()
  const two = new RpcClient()

  const ids: string[] = []
  for (const client of [one, two]) {
    for (let index = 0; index < 5; index += 1) {
      await client.send({ type: 'status' })
    }
    for (const call of recorder.invokes) {
      ids.push((call.args?.payload as Record<string, unknown>).id as string)
    }
    recorder.invokes.length = 0
  }
  assert.equal(new Set(ids).size, ids.length, 'ids collided across clients')
  for (const id of ids) {
    assert.ok(!id.includes(':'), `id must not contain a colon: ${id}`)
  }
})
