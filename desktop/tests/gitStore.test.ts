/** gitStore contract: changes polling, per-file diff caching keyed by
 * path+stage, failure retention, and the 500ms transcript-growth refresh. */

import { beforeEach, mock, test } from 'node:test'
import assert from 'node:assert/strict'
import { useGitStore } from '../src/state/gitStore.ts'
import { useAppStore } from '../src/state/appStore.ts'
import { useChatStore } from '../src/state/chatStore.ts'
import { useWorkPanelStore } from '../src/state/stores.ts'
import type { RpcClient } from '../src/rpc/client.ts'
import type { ChatNode } from '../src/state/chatStore.ts'
import type { GitChangesResult, GitDiffResult } from '../src/rpc/types.ts'

const changesFrame: GitChangesResult = {
  type: 'result',
  id: '',
  ok: true,
  changes: {
    is_repo: true,
    branch: 'main',
    files: [
      { path: 'a.txt', state: 'staged', plus: 2, minus: 0 },
      { path: 'b.txt', state: 'unstaged', plus: 1, minus: 0 },
      { path: 'new.txt', state: 'untracked', plus: null, minus: null },
    ],
    untracked_more: 0,
  },
}

const diffFrame: GitDiffResult = {
  type: 'result',
  id: '',
  ok: true,
  diff: { text: '@@ -0,0 +1,1 @@\n+hello\n', binary: false, truncated: false, missing: false },
}

/** Swap the appStore's client factory for a scripted one; returns the call log. */
function stubClient(frames: Record<string, unknown>) {
  const calls: string[] = []
  const client = {
    async send(payload: { type: string }) {
      calls.push(payload.type)
      const frame = frames[payload.type]
      if (frame === undefined) throw new Error(`unexpected request: ${payload.type}`)
      return frame
    },
  }
  useAppStore.setState({ client: () => client as unknown as RpcClient })
  return calls
}

beforeEach(() => {
  useWorkPanelStore.setState({ open: false })
  useChatStore.getState().reset()
  useGitStore.getState().clear()
})

test('openFile caches the diff by path and stage key', async () => {
  const calls = stubClient({ git_changes: changesFrame, git_diff: diffFrame })

  await useGitStore.getState().loadChanges()
  assert.equal(useGitStore.getState().changes?.files.length, 3)

  await useGitStore.getState().openFile('a.txt', false)
  await useGitStore.getState().openFile('a.txt', false)
  assert.equal(calls.filter((kind) => kind === 'git_diff').length, 1)

  const selected = useGitStore.getState().selected
  assert.deepEqual(selected, { kind: 'file', path: 'a.txt', staged: false })
  assert.deepEqual(useGitStore.getState().diffFor('a.txt:w'), diffFrame.diff)
  assert.equal(useGitStore.getState().diffFor('a.txt:s'), null)
})

test('a failed refresh keeps the previous payload and flags failure', async () => {
  stubClient({ git_changes: changesFrame })
  await useGitStore.getState().loadChanges()

  stubClient({ git_changes: { type: 'result', id: '', ok: false } })
  await useGitStore.getState().loadChanges()

  assert.equal(useGitStore.getState().failed, true)
  assert.equal(useGitStore.getState().changes?.branch, 'main')
})

test('transcript growth refreshes the panel once, debounced 500ms', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const calls = stubClient({ git_changes: changesFrame })
    useWorkPanelStore.setState({ open: true })

    await useGitStore.getState().loadChanges()
    assert.equal(calls.filter((kind) => kind === 'git_changes').length, 1)

    const node: ChatNode = { kind: 'user', id: 'n1', text: 'go', timestamp: 1, steering: false }
    useChatStore.setState({ nodes: [node] })
    useChatStore.setState({ nodes: [node, { ...node, id: 'n2' }] })

    mock.timers.tick(500)
    await Promise.resolve()
    await Promise.resolve()

    assert.equal(calls.filter((kind) => kind === 'git_changes').length, 2)
  } finally {
    mock.timers.reset()
  }
})

test('growth while the panel is closed does not refresh', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const calls = stubClient({ git_changes: changesFrame })
    await useGitStore.getState().loadChanges()

    const node: ChatNode = { kind: 'user', id: 'n1', text: 'go', timestamp: 1, steering: false }
    useChatStore.setState({ nodes: [node] })
    mock.timers.tick(500)
    await Promise.resolve()

    assert.equal(calls.filter((kind) => kind === 'git_changes').length, 1)
  } finally {
    mock.timers.reset()
  }
})

// ---- graph: log paging and keyed refresh -------------------------------------

const logPage1: Record<string, unknown> = {
  type: 'result', id: '', ok: true,
  log: {
    commits: [
      { hash: 'c', short: 'c123456', parents: ['b'], subject: 'third', author: 'a', timestamp: 3, refs: ['main'] },
      { hash: 'b', short: 'b123456', parents: ['a'], subject: 'second', author: 'a', timestamp: 2, refs: [] },
    ],
  },
}
const logPage2: Record<string, unknown> = {
  type: 'result', id: '', ok: true,
  log: {
    commits: [
      { hash: 'a', short: 'a123456', parents: [], subject: 'first', author: 'a', timestamp: 1, refs: [] },
    ],
  },
}
const logHead: Record<string, unknown> = {
  type: 'result', id: '', ok: true,
  log: {
    commits: [
      { hash: 'd', short: 'd123456', parents: ['c'], subject: 'new head', author: 'a', timestamp: 4, refs: ['main'] },
      { hash: 'c', short: 'c123456', parents: ['b'], subject: 'third', author: 'a', timestamp: 3, refs: [] },
      { hash: 'b', short: 'b123456', parents: ['a'], subject: 'second', author: 'a', timestamp: 2, refs: [] },
    ],
  },
}

test('loadLog pages and loadMoreLog appends until a short page', async () => {
  const calls = stubClient({ git_log: logPage1 })
  await useGitStore.getState().loadLog()
  assert.equal(useGitStore.getState().log?.length, 2)
  assert.equal(useGitStore.getState().hasMoreLog, true)

  stubClient({ git_log: logPage2 })
  await useGitStore.getState().loadMoreLog()
  assert.deepEqual(useGitStore.getState().log?.map((c) => c.hash), ['c', 'b', 'a'])
  // A page shorter than the limit ends the history.
  assert.equal(useGitStore.getState().hasMoreLog, false)
  assert.ok(calls.every((kind) => kind === 'git_log'))
})

test('a log refresh prepends new commits and keeps the loaded tail', async () => {
  stubClient({ git_log: logPage1 })
  await useGitStore.getState().loadLog()
  stubClient({ git_log: logPage2 })
  await useGitStore.getState().loadMoreLog()

  stubClient({ git_log: logHead })
  await useGitStore.getState().loadLog()
  assert.deepEqual(useGitStore.getState().log?.map((c) => c.hash), ['d', 'c', 'b', 'a'])
})

test('openCommit caches the show payload by hash', async () => {
  const calls = stubClient({
    git_log: logPage1,
    git_show: {
      type: 'result', id: '', ok: true,
      show: { found: true, short: 'c123456', subject: 'third', files: [] },
    },
  })
  await useGitStore.getState().loadLog()
  await useGitStore.getState().openCommit('c')
  await useGitStore.getState().openCommit('c')
  assert.equal(calls.filter((kind) => kind === 'git_show').length, 1)
  const selected = useGitStore.getState().selected
  assert.deepEqual(selected, { kind: 'commit', hash: 'c' })
  assert.equal(useGitStore.getState().showFor('c')?.subject, 'third')
})
