/** workPanel fullscreen: the toggle flips, and closing the panel also leaves
 * fullscreen so the next open starts as a normal docked rail. */

import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { useWorkPanelStore } from '../src/state/stores.ts'

beforeEach(() => {
  useWorkPanelStore.setState({ open: false, fullscreen: false, tabs: [{ id: 'files', kind: 'files' }], activeId: 'files' })
})

test('toggleFullscreen flips the flag only', () => {
  useWorkPanelStore.getState().toggleFullscreen()
  assert.equal(useWorkPanelStore.getState().fullscreen, true)
  assert.equal(useWorkPanelStore.getState().open, false)
  useWorkPanelStore.getState().toggleFullscreen()
  assert.equal(useWorkPanelStore.getState().fullscreen, false)
})

test('closePanel leaves fullscreen for the next open', () => {
  useWorkPanelStore.setState({ open: true, fullscreen: true })
  useWorkPanelStore.getState().closePanel()
  assert.equal(useWorkPanelStore.getState().open, false)
  assert.equal(useWorkPanelStore.getState().fullscreen, false)
})
