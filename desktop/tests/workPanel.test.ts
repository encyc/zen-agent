import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useWorkPanelStore } from '../src/state/stores.ts'

function reset() {
  useWorkPanelStore.setState({
    open: false,
    tabs: [{ id: 'review', kind: 'review' }],
    activeId: 'review',
  })
}

test('the work panel starts on review and keeps review pinned', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  assert.equal(panel.activeId, 'review')
  panel.closeTab('review')
  assert.deepEqual(useWorkPanelStore.getState().tabs, [{ id: 'review', kind: 'review' }])
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
    ['review', 'subagent:call_1', 'subagent:call_2'],
  )
  assert.equal(state.activeId, 'subagent:call_1')
  assert.equal(state.open, true)
})

test('closing the active tab falls back to review', () => {
  reset()
  const panel = useWorkPanelStore.getState()
  panel.openSubagent({ callId: 'call_1', name: 'explore' })
  panel.closeTab('subagent:call_1')

  const state = useWorkPanelStore.getState()
  assert.equal(state.activeId, 'review')
  assert.deepEqual(state.tabs, [{ id: 'review', kind: 'review' }])
  assert.equal(state.open, true)
})
