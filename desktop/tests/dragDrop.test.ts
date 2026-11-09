/** The composer's Tauri file-drop subscription: every mount must leave
 * exactly one live listener behind, and cleanup must unlisten even when it
 * runs before the async registration settles (StrictMode unmounts the first
 * mount before `listen` resolves). The old inline effect marked itself
 * disposed before checking, so it never unlistened. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { attachDragDrop, type ListenFn } from '../src/ui/composer/dragDrop.ts'

type Handler = (event: { payload: { paths?: unknown } }) => void

/** Same-tick registration: `listen` resolves on the next microtask. */
function fakeListen() {
  const handlers = new Set<Handler>()
  const events: string[] = []
  const listen: ListenFn = (event, handler) => {
    events.push(event)
    handlers.add(handler)
    return Promise.resolve(() => handlers.delete(handler))
  }
  return { listen, handlers, events }
}

/** Deferred registration: the unlisten function only exists once `settle()`
 * is called, which is what a webview round-trip looks like. */
function slowListen() {
  const handlers = new Set<Handler>()
  const settles: Array<() => void> = []
  const listen: ListenFn = (event, handler) => {
    handlers.add(handler)
    return new Promise<() => void>((resolve) => {
      settles.push(() => resolve(() => handlers.delete(handler)))
    })
  }
  const settle = () => {
    for (const go of settles.splice(0)) go()
  }
  return { listen, handlers, settle }
}

const flush = () => new Promise<void>((resolve) => setImmediate(resolve))

test('subscribes to the tauri drop event and forwards every path', () => {
  const fake = fakeListen()
  const batches: string[][] = []
  attachDragDrop((paths) => batches.push(paths), fake.listen)

  assert.deepEqual(fake.events, ['tauri://drag-drop'])
  assert.equal(fake.handlers.size, 1)
  for (const handler of fake.handlers) {
    handler({ payload: { paths: ['/tmp/a.png', '/tmp/b.txt'] } })
  }
  assert.deepEqual(batches, [['/tmp/a.png', '/tmp/b.txt']])
})

test('a payload without a path list is ignored', () => {
  const fake = fakeListen()
  const batches: string[][] = []
  attachDragDrop((paths) => batches.push(paths), fake.listen)

  for (const handler of fake.handlers) handler({ payload: {} })
  assert.deepEqual(batches, [])
})

test('cleanup unlistens', async () => {
  const fake = fakeListen()
  const detach = attachDragDrop(() => {}, fake.listen)
  await flush()

  detach()
  assert.equal(fake.handlers.size, 0)
})

test('cleanup before the registration settles still unlistens', async () => {
  const fake = slowListen()
  const detach = attachDragDrop(() => {}, fake.listen)
  assert.equal(fake.handlers.size, 1)

  detach()
  // The registration promise has not settled yet; the old code left this
  // listener registered forever.
  assert.equal(fake.handlers.size, 1)
  fake.settle()
  await flush()

  assert.equal(fake.handlers.size, 0)
})

test('a drop after cleanup is ignored even while registration is in flight', () => {
  const fake = slowListen()
  const batches: string[][] = []
  const detach = attachDragDrop((paths) => batches.push(paths), fake.listen)
  detach()

  for (const handler of fake.handlers) {
    handler({ payload: { paths: ['/tmp/late.png'] } })
  }
  assert.deepEqual(batches, [])
})

test('a strict-mode remount leaves exactly one live listener', async () => {
  const fake = slowListen()
  const batches: string[][] = []
  const first = attachDragDrop((paths) => batches.push(['first', ...paths]), fake.listen)
  first() // StrictMode cleanup runs before the first registration settles.
  attachDragDrop((paths) => batches.push(['second', ...paths]), fake.listen)
  fake.settle()
  await flush()

  assert.equal(fake.handlers.size, 1)
  for (const handler of fake.handlers) {
    handler({ payload: { paths: ['/tmp/a.png'] } })
  }
  assert.deepEqual(batches, [['second', '/tmp/a.png']])
})
