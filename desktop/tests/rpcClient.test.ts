/** The two RPC-client contracts the boot path leans on: `send` is the single
 * place a wire answer becomes a typed domain frame, and concurrent `start()`
 * calls must register the event listeners once instead of stacking them. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { RpcClient, _useBridge, type Bridge } from '../src/rpc/client.ts'
import type { StatusResult } from '../src/rpc/types.ts'

interface Recorder {
  bridge: Bridge
  invokes: { command: string; args?: Record<string, unknown> }[]
  listens: string[]
}

/** A fake Tauri bridge: records every call, answers `invoke` from `script`. */
function record(script: (command: string) => unknown): Recorder {
  const invokes: Recorder['invokes'] = []
  const listens: Recorder['listens'] = []
  return {
    invokes,
    listens,
    bridge: {
      async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
        invokes.push({ command, args })
        return script(command) as T
      },
      async listen<T>(): Promise<() => void> {
        listens.push('listen')
        await new Promise((resolve) => setTimeout(resolve, 1))
        return () => {}
      },
      async relaunch(): Promise<void> {},
    },
  }
}

test('send() forwards the request with a correlation id and resolves the typed frame', async () => {
  const recorder = record(() => ({ type: 'result', id: 'r1', ok: true, running: false }))
  _useBridge(recorder.bridge)
  const client = new RpcClient()

  const status = await client.send<StatusResult>({ type: 'status' }, 2500)

  const [invoke] = recorder.invokes
  assert.equal(invoke.command, 'rpc_send')
  assert.equal((invoke.args?.payload as Record<string, unknown>).type, 'status')
  assert.match((invoke.args?.payload as Record<string, unknown>).id as string, /^r[a-z0-9]{4}\d+$/)
  assert.equal(status.running, false)
})

test('send() without a timeout passes null so the shell keeps its own default', async () => {
  const recorder = record(() => ({ type: 'result', id: 'r1', ok: true }))
  _useBridge(recorder.bridge)
  const client = new RpcClient()

  await client.send({ type: 'hello' })

  assert.equal(recorder.invokes.length, 1)
  assert.equal((recorder.invokes[0]?.args?.payload as Record<string, unknown>).type, 'hello')
})

test('concurrent start() calls register the three event listeners once', async () => {
  const recorder = record((command) => (command === 'rpc_start' ? { pid: 41 } : null))
  _useBridge(recorder.bridge)
  const client = new RpcClient()

  const pids = await Promise.all([client.start(), client.start(), client.start()])

  assert.deepEqual(pids, [41, 41, 41])
  assert.equal(recorder.listens.length, 3)
})

test('a partial registration is released before the retry registers again', async () => {
  // The fake tracks live listeners: a retry must end with exactly one of each,
  // never the first attempt's still-attached `rpc:frame` plus the new one.
  const live = new Map<string, number>()
  let calls = 0
  _useBridge({
    async invoke<T>(command: string): Promise<T> {
      return (command === 'rpc_start' ? { pid: 9 } : null) as T
    },
    async listen<T>(event: string): Promise<() => void> {
      calls += 1
      // Reach `rpc:stderr` before failing, so `rpc:frame` already succeeded.
      if (calls === 2) throw new Error('event plugin unavailable')
      live.set(event, (live.get(event) ?? 0) + 1)
      return () => live.set(event, (live.get(event) ?? 1) - 1)
    },
    async relaunch(): Promise<void> {},
  })
  const client = new RpcClient()

  await assert.rejects(client.start())
  assert.equal(live.get('rpc:frame'), 0, 'the first attempt left its frame listener attached')

  assert.equal(await client.start(), 9)
  assert.deepEqual(Object.fromEntries([...live].sort()), {
    'rpc:exit': 1,
    'rpc:frame': 1,
    'rpc:stderr': 1,
  })
})

test('a failed listener registration does not poison the later starts', async () => {
  const listens: string[] = []
  _useBridge({
    async invoke<T>(command: string): Promise<T> {
      return (command === 'rpc_start' ? { pid: 7 } : null) as T
    },
    async listen<T>(event: string): Promise<() => void> {
      listens.push(event)
      if (listens.length === 1) throw new Error('event plugin unavailable')
      return () => {}
    },
    async relaunch(): Promise<void> {},
  })
  const client = new RpcClient()

  await assert.rejects(client.start())
  assert.equal(await client.start(), 7)
  assert.deepEqual(listens, ['rpc:frame', 'rpc:frame', 'rpc:stderr', 'rpc:exit'])
})

test('routed requests carry the active project; host ops stay untagged', async () => {
  const recorder = record(() => ({ type: 'result', id: 'r', ok: true }))
  _useBridge(recorder.bridge)
  const client = new RpcClient()
  client.setProject('/work/p')

  await client.send({ type: 'status' })
  await client.send({ type: 'hello' })
  await client.send({ type: 'project', op: 'switch', root: '/work/q' })

  const payloads = recorder.invokes.map(
    (call) => call.args?.payload as Record<string, unknown>,
  )
  assert.deepEqual(
    payloads.map((payload) => [payload.type, payload.project]),
    [
      ['status', '/work/p'],
      ['hello', undefined],
      ['project', undefined],
    ],
  )
  const ids = payloads.map((payload) => payload.id as string)
  assert.equal(new Set(ids).size, 3)
})

test('handshake adopts the host’s active project for later requests', async () => {
  const recorder = record(() => ({
    type: 'result',
    id: 'r1',
    ok: true,
    protocol_version: 3,
    active: '/work/q',
  }))
  _useBridge(recorder.bridge)
  const client = new RpcClient()

  assert.equal(await client.handshake(), '/work/q')
  assert.equal(client.activeProject(), '/work/q')

  await client.send({ type: 'status' })
  const payload = recorder.invokes.at(-1)?.args?.payload as Record<string, unknown>
  assert.equal(payload.type, 'status')
  assert.equal(payload.project, '/work/q')
  assert.match(payload.id as string, /^r[a-z0-9]{4}\d+$/)
})
