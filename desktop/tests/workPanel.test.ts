import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useWorkPanelStore } from '../src/state/stores.ts'

function reset() {
  useWorkPanelStore.setState({
    open: false,
    tabs: [
      { id: 'review', kind: 'review' },
      { id: 'files', kind: 'files' },
    ],
    activeId: 'files',
  })
}

test('the work panel starts on files', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  assert.equal(panel.activeId, 'files')
})

test('opening the panel always lands on files, even when review was last active', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.activate('review')
  assert.equal(useWorkPanelStore.getState().activeId, 'review')
  panel.closePanel()
  panel.openPanel()

  const state = useWorkPanelStore.getState()
  assert.equal(state.open, true)
  assert.equal(state.activeId, 'files')
})

test('opening the panel restores files when its tab was closed', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.closeTab('files')
  panel.closePanel()
  panel.openPanel()

  const state = useWorkPanelStore.getState()
  assert.equal(state.activeId, 'files')
  assert.deepEqual(
    state.tabs.map((tab) => tab.id),
    ['review', 'files'],
  )
})

test('review is closable like any other tab', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.activate('review')
  panel.closeTab('review')

  const state = useWorkPanelStore.getState()
  assert.deepEqual(state.tabs, [{ id: 'files', kind: 'files' }])
  assert.equal(state.activeId, 'files')
  assert.equal(state.open, true)
})

test('closing the last tab closes the panel', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.closeTab('review')
  panel.closeTab('files')

  const state = useWorkPanelStore.getState()
  assert.deepEqual(state.tabs, [])
  assert.equal(state.open, false)

  useWorkPanelStore.getState().openPanel()
  const reopened = useWorkPanelStore.getState()
  assert.equal(reopened.open, true)
  assert.equal(reopened.activeId, 'files')
  assert.deepEqual(
    reopened.tabs.map((tab) => tab.id),
    ['files'],
  )
})

test('files and worktree are singleton tabs', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.showTab('files')
  assert.equal(useWorkPanelStore.getState().open, true)
  assert.equal(useWorkPanelStore.getState().activeId, 'files')
  panel.showTab('worktree')
  panel.showTab('files')
  assert.deepEqual(
    useWorkPanelStore.getState().tabs.map((tab) => tab.id),
    ['review', 'files', 'worktree'],
  )
  assert.equal(useWorkPanelStore.getState().activeId, 'files')
})

test('each subagent call gets its own tab and reactivates on reopen', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.openSubagent({ callId: 'call_1', name: 'explore' })
  panel.openSubagent({ callId: 'call_2', name: 'review' })
  panel.openSubagent({ callId: 'call_1', name: 'explore' })

  const state = useWorkPanelStore.getState()
  assert.deepEqual(
    state.tabs.map((tab) => tab.id),
    ['review', 'files', 'subagent:call_1', 'subagent:call_2'],
  )
  assert.equal(state.activeId, 'subagent:call_1')
  assert.equal(state.open, true)
})

test('closing the active tab falls back to files', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.openSubagent({ callId: 'call_1', name: 'explore' })
  panel.closeTab('subagent:call_1')

  const state = useWorkPanelStore.getState()
  assert.equal(state.activeId, 'files')
  assert.deepEqual(
    state.tabs.map((tab) => tab.id),
    ['review', 'files'],
  )
  assert.equal(state.open, true)
})

test('closing files falls back to review', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.closeTab('files')

  const state = useWorkPanelStore.getState()
  assert.equal(state.activeId, 'review')
  assert.deepEqual(state.tabs, [{ id: 'review', kind: 'review' }])
})
