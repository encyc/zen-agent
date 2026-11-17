/** The detach seam: the drag decision is pure, the URL encodes the boot
 * override, and a failed window creation (a plain browser) is a false return
 * with a notice — never a thrown error through the UI. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  detachSession,
  detachedWindowUrl,
  outsideDrop,
  setDetachBridge,
  type DetachBridge,
} from '../src/state/detachWindow.ts'
import { useNoticeStore } from '../src/state/stores.ts'

const OURS = 'application/x-zen-session'

function bounds() {
  return { x: 100, y: 100, width: 800, height: 600 }
}

test('outsideDrop accepts only our drag type ending beyond the window', () => {
  const ours = { screenX: 20, screenY: 200, dataTypes: [OURS] }
  assert.equal(outsideDrop(ours, bounds()), true)
  assert.equal(
    outsideDrop({ screenX: 400, screenY: 200, dataTypes: [OURS] }, bounds()),
    false,
    'a drop inside the window is not a detach',
  )
  assert.equal(
    outsideDrop({ screenX: 20, screenY: 200, dataTypes: ['text/plain'] }, bounds()),
    false,
    'some other drag (a file) never detaches a session',
  )
  assert.equal(
    outsideDrop({ screenX: 20, screenY: 2000, dataTypes: [OURS] }, bounds()),
    true,
  )
})

test('the detached window URL carries the boot override, session and project', () => {
  const url = detachedWindowUrl('/work/p/sessions/b.jsonl', '/work/p')
  assert.ok(url.includes('boot=session'))
  assert.ok(url.includes(`session=${encodeURIComponent('/work/p/sessions/b.jsonl')}`))
  assert.ok(url.includes(`project=${encodeURIComponent('/work/p')}`))
  assert.equal(detachedWindowUrl('/x.jsonl', null).includes('project='), false)
})

test('detachSession creates the window and broadcasts the detach', async () => {
  const created: { label: string; url: string; title: string }[] = []
  const emitted: { event: string; payload: unknown }[] = []
  const fake: DetachBridge = {
    async createWindow(options) {
      created.push(options)
    },
    async emit(event, payload) {
      emitted.push({ event, payload })
    },
  }
  setDetachBridge(fake)

  const opened = await detachSession({
    session: '/work/p/sessions/b.jsonl',
    title: 'B 会话',
    project: '/work/p',
    locale: 'zh',
  })

  assert.equal(opened, true)
  assert.equal(created.length, 1)
  assert.ok(created[0]?.label.startsWith('session-'))
  assert.ok(created[0]?.url.includes('boot=session'))
  assert.ok(created[0]?.title.includes('B 会话'))
  assert.deepEqual(emitted, [
    { event: 'zen:session-detached', payload: { session: '/work/p/sessions/b.jsonl' } },
  ])
  setDetachBridge(null)
})

test('a failed creation is a notice and false, never a throw', async () => {
  useNoticeStore.getState().dismiss()
  setDetachBridge({
    async createWindow() {
      throw new Error('no webview plugin here')
    },
    async emit() {},
  })

  const opened = await detachSession({
    session: '/x.jsonl',
    title: 'x',
    project: null,
    locale: 'en',
  })

  assert.equal(opened, false)
  assert.equal(useNoticeStore.getState().notice?.text, 'Detached windows only work in the desktop app.')
  setDetachBridge(null)
})
