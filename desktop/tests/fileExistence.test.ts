/** The file-existence store: one probe per path, verdicts only from settled
 * answers, refs collected from rendered markup. Run with
 * `node --test tests/fileExistence.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  collectFileRefs,
  useFileExistenceStore,
} from '../src/state/fileExistence.ts'

interface SendCall {
  path: string
  resolve: (result: unknown) => void
}

/** A sender that records calls and lets each test settle them by hand. */
function scriptedSender() {
  const calls: SendCall[] = []
  const send = (message: { type: string; path: string }): Promise<unknown> =>
    new Promise((resolve) => {
      calls.push({ path: message.path, resolve })
    })
  return { calls, send }
}

function freshStore() {
  useFileExistenceStore.getState().reset()
}

/** The probe body runs on a microtask, so a synchronous test must yield once
 * before `send` has been called and `calls` is populated. */
const tick = (): Promise<void> => new Promise((resolve) => setImmediate(resolve))

test('collectFileRefs reads the paths back out of rendered markup', () => {
  const html =
    '<code class="md-file-ref" data-file-path="src/a.ts">src/a.ts</code>' +
    '<code class="md-file-ref" data-file-path="src/a.ts">dup</code>' +
    '<code>plain</code>'
  assert.deepEqual(collectFileRefs(html), ['src/a.ts'])
})

test('collectFileRefs unescapes what escapeAttr encoded', () => {
  const html = '<code data-file-path="src/my &quot;weird&quot; &lt;file&gt;.ts">x</code>'
  assert.deepEqual(collectFileRefs(html), ['src/my "weird" <file>.ts'])
})

test('collectFileRefs skips empty attributes', () => {
  assert.deepEqual(collectFileRefs('<code data-file-path="">x</code>'), [])
})

test('settled answers become verdicts, one probe per path', async () => {
  freshStore()
  const { calls, send } = scriptedSender()
  useFileExistenceStore.getState().ensure(['src/a.ts', 'src/gone.ts'], send)
  await tick()
  assert.deepEqual(
    calls.map((call) => call.path),
    ['src/a.ts', 'src/gone.ts'],
  )
  calls.find((call) => call.path === 'src/a.ts')?.resolve({ ok: true, exists: true })
  calls.find((call) => call.path === 'src/gone.ts')?.resolve({ ok: true, exists: false })
  await useFileExistenceStore.getState().drain()

  assert.deepEqual(useFileExistenceStore.getState().verdicts, {
    'src/a.ts': true,
    'src/gone.ts': false,
  })

  // A settled path never probes again.
  useFileExistenceStore.getState().ensure(['src/a.ts', 'src/gone.ts'], send)
  await useFileExistenceStore.getState().drain()
  assert.equal(calls.length, 2)
})

test('a path in flight is not probed twice', async () => {
  freshStore()
  const { calls, send } = scriptedSender()
  useFileExistenceStore.getState().ensure(['src/a.ts'], send)
  useFileExistenceStore.getState().ensure(['src/a.ts'], send)
  await tick()
  assert.equal(calls.length, 1)
  calls[0]?.resolve({ ok: true, exists: true })
  await useFileExistenceStore.getState().drain()
  assert.equal(calls.length, 1)
})

test('an fs_error frame is a verdict: the server refused the path outright', async () => {
  freshStore()
  const { calls, send } = scriptedSender()
  useFileExistenceStore.getState().ensure(['../outside'], send)
  await tick()
  calls[0]?.resolve({ type: 'error', code: 'fs_error', message: 'exists failed' })
  await useFileExistenceStore.getState().drain()
  assert.deepEqual(useFileExistenceStore.getState().verdicts, { '../outside': false })
})

test('a failed transport leaves no verdict and may be probed again', async () => {
  freshStore()
  const { calls, send } = scriptedSender()
  useFileExistenceStore.getState().ensure(['src/a.ts'], send)
  await tick()
  calls[0]?.resolve({ type: 'error', message: 'session gone' })
  await useFileExistenceStore.getState().drain()
  assert.deepEqual(useFileExistenceStore.getState().verdicts, {})

  // The retry path: with no flight and no verdict, ensure probes again.
  useFileExistenceStore.getState().ensure(['src/a.ts'], send)
  await tick()
  assert.equal(calls.length, 2)
  calls[1]?.resolve({ ok: true, exists: false })
  await useFileExistenceStore.getState().drain()
  assert.deepEqual(useFileExistenceStore.getState().verdicts, { 'src/a.ts': false })
})

test('a throwing sender leaves no verdict either', async () => {
  freshStore()
  useFileExistenceStore.getState().ensure(['src/a.ts'], () => {
    throw new Error('client() with no session')
  })
  await useFileExistenceStore.getState().drain()
  assert.deepEqual(useFileExistenceStore.getState().verdicts, {})
})
