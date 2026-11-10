/** Work panel file requests: a chat file reference opens the rail's Files tab
 * and asks its viewer for one path. Run with
 * `node --test tests/workPanelFiles.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { useWorkPanelStore } from '../src/state/stores.ts'

test('opening a file reveals the files tab and queues a read request', () => {
  useWorkPanelStore.getState().closePanel()
  useWorkPanelStore.getState().openFile('src/a.ts')

  const next = useWorkPanelStore.getState()
  assert.equal(next.open, true)
  assert.equal(next.activeId, 'files')
  assert.equal(next.fileRequest?.path, 'src/a.ts')
  assert.ok(next.tabs.some((tab) => tab.kind === 'files'))
})

test('requesting the same file again bumps the nonce so the viewer reopens', () => {
  useWorkPanelStore.getState().openFile('src/a.ts')
  const first = useWorkPanelStore.getState().fileRequest?.nonce ?? 0
  useWorkPanelStore.getState().openFile('src/a.ts')
  const second = useWorkPanelStore.getState().fileRequest?.nonce ?? 0

  assert.ok(second > first, `nonce ${second} should exceed ${first}`)
})

test('consuming a request clears it', () => {
  useWorkPanelStore.getState().openFile('src/a.ts')
  useWorkPanelStore.getState().consumeFileRequest()

  assert.equal(useWorkPanelStore.getState().fileRequest, null)
})
